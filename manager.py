# Core management class
# tides_pipe/manager.py
import importlib
import os
import logging
import signal
from typing import List, Optional
import pathlib
import yaml

from importlib import import_module
import asyncio
import time

from tides_pipe.modules.classifiers.snid_handler import SnidHandler
from tides_pipe.modules.classifiers.snid_defaults import snid_params_from_config
from tides_pipe.modules.classifiers.client import classify_snid_async, classify_ngsf_async
from tides_pipe.modules.classifiers.classification_store import save_result
from tides_pipe.utils.paths import spectra_night_dir as util_spectra_night_dir, spectrum_path as util_spectrum_path
from tides_pipe.utils.slack import send_slack_message

# Optional DB/status helpers (don’t break if missing)
try:
    from tides_pipe.utils import db as dbutil  # provides load_creds/connect
except Exception:
    dbutil = None
try:
    from tides_pipe.modules import status_store  # provides upsert_status/add_event
except Exception:
    status_store = None

_STOP = False

def _handle_sigterm(signum, frame):
    global _STOP
    _STOP = True

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)

def setup_logger(night, config):
    """
    Per-night logger. Writes to: <spectra_dir>/<night>/logs/<night>.log
    Falls back to <spectra_dir>/logs/pipeline.log if night is empty.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("tides_manager")
    logger.setLevel(level)
    logger.propagate = False

    dp = (config.get("data_paths") or {})
    spectra_dir = dp.get("spectra_dir", "/data/spectra")
    if night:
        log_dir = os.path.join(spectra_dir, str(night), "logs")
        log_file = os.path.join(log_dir, f"{night}.log")
    else:
        log_dir = os.path.join(spectra_dir, "logs")
        log_file = os.path.join(log_dir, "pipeline.log")

    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    # File handler (add once)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fh = logging.FileHandler(log_file)
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    # Console handler (add once)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(level)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger.info(f"[config] Log file: {log_file}")
    return logger

class PipelineManager:
    def __init__(self, modules: Optional[List[str]] = None, config_path: Optional[str] = None):
        # Allow Docker override of config path
        self.config_path = config_path or os.getenv("TIDES_CONFIG", "config/config.yml")
        self.config = self.load_config()

        # Load pluggable steps (modules or callables). Falls back to legacy modules list.
        self.steps = self.load_steps(modules)
        # Status tracking
        self.current_night: str = ""
        self._status_conn = None
        self._db_conn = None  # Reusable DB connection
        self.snid = SnidHandler()

    def _get_db_connection(self):
        """Get or create a reusable DB connection."""
        if self._db_conn is None and dbutil:
            try:
                creds = dbutil.load_creds(self.config)
                self._db_conn = dbutil.connect(creds)
            except Exception:
                pass
        return self._db_conn
    
    def _close_db_connection(self):
        """Close the DB connection if open."""
        if self._db_conn:
            try:
                self._db_conn.close()
            except Exception:
                pass
            finally:
                self._db_conn = None

    def load_config(self):
        if not os.path.exists(self.config_path):
            # Minimal default to keep running in Docker if config is mounted later
            return {"modules": []}
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f) or {"modules": []}

    def _merge_lists(self, a, b):
        seen, out = set(), []
        for x in (a or []) + (b or []):
            if x not in seen:
                out.append(x)
                seen.add(str(x))
        return out

    def _import_callable(self, dotted: str):
        """
        Import a callable referenced as 'pkg.mod:func'.
        """
        if ":" not in dotted:
            raise ValueError(f"Callable must be 'pkg.mod:func', got {dotted}")
        mod_path, func_name = dotted.split(":", 1)
        mod = import_module(mod_path)
        fn = getattr(mod, func_name)
        if not callable(fn):
            raise TypeError(f"{dotted} is not callable")
        return fn

    def load_steps(self, modules_override: Optional[List[str]] = None):
        """
        Supports:
        - legacy: config['modules'] = ['data_ingestion', 'classification_api']
        - new: config['steps'] = [{type: module, import: data_ingestion}, {type: callable, name: classification_api, func: pkg:run, params:{}}]
        Returns list of dicts: {name, kind, runner, params}
        """
        steps_cfg = self.config.get("steps")
        if steps_cfg:
            steps = []
            for s in steps_cfg:
                kind = s.get("type", "module")
                name = s.get("name") or s.get("import") or s.get("func")
                params = s.get("params") or {}
                if kind == "module":
                    imp = s.get("import")
                    modpath = imp if "." in str(imp) else f"tides_pipe.modules.{imp}"
                    try:
                        mod = import_module(modpath)
                        runner = getattr(mod, "run")
                        steps.append({"name": name, "kind": "module", "runner": runner, "params": params})
                    except Exception as e:
                        logging.getLogger("tides_manager").error(f"Failed to load module '{imp}': {e}", exc_info=True)
                elif kind == "callable":
                    func = s.get("func")
                    try:
                        runner = self._import_callable(func)
                        steps.append({"name": name, "kind": "callable", "runner": runner, "params": params})
                    except Exception as e:
                        logging.getLogger("tides_manager").error(f"Failed to import callable '{func}': {e}", exc_info=True)
                else:
                    logging.getLogger("tides_manager").warning(f"Unknown step kind '{kind}' for {s}")
            return steps

        # Legacy fallback using 'modules' list
        config_modules = self.config.get("modules", [])
        modules = self._merge_lists(modules_override, config_modules)
        steps = []
        for name in modules:
            if name == "classification_api":
                # Built-in pseudo-step handled below
                steps.append({"name": "classification_api", "kind": "builtin", "runner": None, "params": {}})
                continue
            try:
                mod = import_module(f"tides_pipe.modules.{name}")
                runner = getattr(mod, "run")
                steps.append({"name": name, "kind": "module", "runner": runner, "params": {}})
            except Exception as e:
                logging.getLogger("tides_manager").error(f"Failed to load module '{name}': {e}", exc_info=True)
        return steps

    def _spectra_night_dir(self) -> str:
        return util_spectra_night_dir(self.config, self.current_night, ensure=True)

    def _spectrum_path(self, tides_id: str | int) -> str:
        return util_spectrum_path(self.config, self.current_night, tides_id, ensure_dir=True)

    def _update_status(self, state: str, message: str = "", **kwargs):
        """Update pipeline status if status tracking is available."""
        if self._status_conn and self.current_night and status_store:
            try:
                status_store.upsert_status(
                    self._status_conn,
                    self.current_night,
                    state,
                    message=message,
                    **kwargs
                )
            except Exception:
                pass
    
    def _add_status_event(self, module: str, level: str, message: str):
        """Add a status event if status tracking is available."""
        if self._status_conn and self.current_night and status_store:
            try:
                status_store.add_event(
                    self._status_conn,
                    self.current_night,
                    module,
                    level,
                    message
                )
            except Exception:
                pass

    
    def _logs_dir(self) -> str:
        """
        Logs/DONE flags directory for current night:
        <spectra_dir>/<night>/logs
        """
        base = util_spectra_night_dir(self.config, self.current_night, ensure=True)
        logs_dir = os.path.join(base, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        return logs_dir

    def set_module_done(self, module_name: str, status: bool):
        """
        Record DONE/FAILED for a pseudo-module (e.g., classification_api) to integrate with check_module_done().
        """
        logs_dir = self._logs_dir()
        module_done_file = os.path.join(logs_dir, f"{module_name}_DONE.txt")
        with open(module_done_file, 'w') as f:
            f.write("TRUE\n" if status else "FALSE\n")

    def check_module_done(self, module_name: str) -> bool:
        """
        Check if a module has been marked as done.
        """
        logs_dir = self._logs_dir()
        module_done_file = os.path.join(logs_dir, f"{module_name}_DONE.txt")
        if os.path.exists(module_done_file):
            with open(module_done_file, 'r') as f:
                return f.read().strip().upper().startswith("TRUE")
        return False

    async def _run_classifiers_api(self, obj_names: list[str | int], logger: logging.Logger, spectrum_map: dict, snid_params: dict | None = None, ngsf_params: dict | None = None):
        """
        Call classifier microservices (SNID/NGSF) via HTTP for each tides_id concurrently,
        then persist results to the remote DB.
        """
        logger.info(f"[classification_api] Starting classification for {len(obj_names or [])} objects")
        
        conn = self._get_db_connection()
        if not conn:
            logger.error(f"[classification_api] DB connection failed; will skip persistence")

        async def tagged(method: str, tides_id: int, coro):
            res = await coro
            return method, tides_id, res

        tasks = []
        classification_config = self.config.get('classification', {})
        enabled_classifiers = classification_config.get('enabled_classifiers', ['snid', 'ngsf'])
        logger.info(f"[classification_api] Enabled classifiers: {enabled_classifiers}")
        
        # Check if we should skip TEMP IDs in test mode
        test_mode = os.getenv('TEST', '').lower() == 'true'
        
        for tid in obj_names or []:
            spec_info = spectrum_map.get(str(tid))
            if not spec_info:
                logger.warning(f"[classification_api] No spectrum info for {tid}")
                continue
            
            # Skip TEMP IDs in test mode (no match in tides_master)
            if test_mode and spec_info.get('is_temp_id', False):
                logger.info(f"[classification_api] Skipping TEMP ID {tid} in test mode (no tides_master match)")
                continue
            
            spath = spec_info['filepath']
            tides_specid = spec_info['tides_specid']
            
            if not os.path.exists(spath):
                logger.warning(f"[classification_api] Spectrum not found for {tid}: {spath}")
                continue
            
            tid_int = int(tid)
            if 'snid' in enabled_classifiers:
                tasks.append(tagged("snid", tid_int, classify_snid_async(tid_int, spath, snid_params or {})))
            if 'ngsf' in enabled_classifiers:
                tasks.append(tagged("ngsf", tid_int, classify_ngsf_async(tid_int, spath, ngsf_params or {})))

        classified_ok = 0
        ok = True
        logger.info(f"[classification_api] Processing {len(tasks)} classification tasks")
        
        for coro in asyncio.as_completed(tasks):
            try:
                method, tid_int, res = await coro
                logger.info(f"[classification_api] {method} result for {tid_int}: {res}")
                if conn:
                    save_result(conn, tid_int, method, res, logger=logger)
                classified_ok += 1
            except Exception as e:
                ok = False
                logger.error(f"[classification_api] Classifier call failed: {e}", exc_info=True)
        
        self._update_status(
            "classifying",
            "Classification completed" if ok else "Classification completed with errors",
            classified=classified_ok
        )
        self._add_status_event(
            "classification_api",
            "INFO" if ok else "ERROR",
            f"classified {classified_ok} objects"
        )
        self.set_module_done("classification_api", ok)

    def send_to_db(self, classification_results_file, obj_names, logger):
        """Send classification results from file to database."""
        if not classification_results_file or not os.path.exists(classification_results_file):
            logger.warning(f"Classification results file not found: {classification_results_file}")
            return
        
        try:
            import pandas as pd
            df = pd.read_csv(classification_results_file)
            logger.info(f"Reading classification results from {classification_results_file}")
            
            # Get database connection
            try:
                creds = dbutil.load_creds(self.config)
                conn = dbutil.connect(creds)
            except Exception as e:
                logger.error(f"Could not connect to database for saving classification results: {e}")
                return
                
            # Process each row in the results file
            for _, row in df.iterrows():
                tid_int = int(row.get('obj_name', 0))  # Assuming obj_name contains the tides_id
                
                # Check for different classification methods in the row
                enabled_classifiers = self.config.get('classification', {}).get('enabled_classifiers', ['snid', 'ngsf'])
                for method in enabled_classifiers:
                    result_key = f'auto_class_{method}'
                    prob_key = f'auto_class_prob_{method}'
                    subclass_key = f'auto_class_subclass_{method}'
                    
                    if result_key in row and pd.notna(row[result_key]):
                        result = {
                            'result': row[result_key],
                            'probability': row.get(prob_key, 0.0),
                            'subclass': row.get(subclass_key, '')
                        }
                        save_result(conn, tid_int, method, result, logger=logger)
                        logger.info(f"Saved {method} result for {tid_int}: {result}")
            
            conn.close()
            logger.info(f"Successfully saved classification results from {classification_results_file}")
            
        except Exception as e:
            logger.error(f"Failed to send classification results to database: {e}", exc_info=True)

    def signal_pipeline_done(self, logger):
        """Signal that pipeline processing is complete."""
        logger.info("Pipeline processing complete")
        # You can add additional logic here like writing a done file or updating status
        
    def clear_pipeline_done_signal(self, logger):
        """Clear any pipeline done signals."""
        logger.info("Clearing pipeline done signals")
        # You can add additional logic here like removing done files

    # Defaults aligned with your Django SnidParamsForm
    def _snid_default_params(self) -> dict:
        return snid_params_from_config(self.config)

    def _limit_for_test(self, obj_names: list[str], logger):
        cfg = (self.config.get("classification") or {})
        if not cfg.get("test"):
            return obj_names
        limit = int(cfg.get("max", 5))
        if limit and len(obj_names) > limit:
            logger.info(f"[classification_api] test=True; limiting to first {limit} of {len(obj_names)} objects")
            return obj_names[:limit]
        return obj_names

    def _run_snid_classification(self, night: str, obj_names: list, spectrum_map: dict, logger) -> list[dict]:
        obj_names = self._limit_for_test([str(x) for x in obj_names], logger)
        params = self._snid_default_params()
        results = []
        
        for obj_id in obj_names:
            spec_info = spectrum_map.get(str(obj_id))
            if not spec_info:
                logger.warning(f"[classification_api] No spectrum info for {obj_id}")
                continue
            
            spath = spec_info['filepath']
            tides_specid = spec_info['tides_specid']
            
            try:
                logger.info(f"[classification_api] SNID classify obj={obj_id} tides_specid={tides_specid} path={spath}")
                res = self.snid.classify(spath, night, obj_id, tides_specid=tides_specid, snid_params=params)
                results.append({"obj_id": obj_id, "tides_specid": tides_specid, "snid": res})
            except Exception as e:
                logger.exception(f"[classification_api] SNID failed for {obj_id}: {e}")
        
        return results

    def run(self, night=None, objects=None, one_shot: bool = False, sleep_seconds: int = 60, env: str = "operations"):
        # Inject env into config so modules can use it (e.g. for path construction)
        self.config["env"] = env
        # Ensure spectra_dir in config is env-aware so util paths align with ingestion
        dp = self.config.setdefault("data_paths", {})
        base = dp.get("spectra_dir") or "/data/spectra"
        allowed_envs = ("operations", "dev")
        tail = os.path.basename(os.path.normpath(base))
        base_root = os.path.dirname(os.path.normpath(base)) if tail in allowed_envs else base
        dp["spectra_dir"] = os.path.join(base_root, env)
        
        logger = setup_logger(night, self.config)
        self.current_night = str(night or "")
        obj_names: list = []  # tides_id list for legacy
        spectrum_map: dict = {}  # tides_id -> {tides_specid, filepath}

        try:
            sdir = util_spectra_night_dir(self.config, self.current_night, ensure=True)
            logger.info(f"[config] spectra_night_dir={sdir} (real={os.path.realpath(sdir)})")
        except Exception as e:
            logger.error(f"[config] Invalid spectra_night_dir: {e}")
            return

        logger.info(f"Pipeline starting (night={night}, one_shot={one_shot}, sleep={sleep_seconds}s, env={env})")
        
        # Initialize status tracking
        try:
            if dbutil:
                self._status_conn = self._get_db_connection()
            self._update_status("running", "Manager started")
            self._add_status_event("manager", "INFO", "Manager loop starting")
        except Exception as e:
            logger.warning(f"Status DB not available: {e}")

        while True:
            if _STOP:
                logger.info("Shutdown signal received; exiting manager loop.")
                self._add_status_event("manager", "INFO", "Shutdown requested")
                self._update_status("error", "Interrupted", finished=True)
                break

            all_done = True
            classification_results_file = None

            for step in self.steps:
                name = step["name"]
                kind = step.get("kind", "module")

                if self.check_module_done(name):
                    logger.info(f"[{name}] Step already completed, skipping")
                    continue

                if kind == "module" and name == "data_ingestion":
                    try:
                        logger.info(f"[{name}] Running ingestion for night {self.current_night}")
                        returned = step["runner"](night=self.current_night, logger=logger, config=self.config)
                        if returned is not None:
                            for item in returned:
                                tid = str(item['tides_id'])
                                obj_names.append(tid)
                                spectrum_map[tid] = {
                                    'tides_specid': item['tides_specid'],
                                    'filepath': item['filepath']
                                }
                            logger.info(f"[{name}] Ingestion produced {len(obj_names)} objects with tides_specid mapping")
                        else:
                            logger.warning(f"[{name}] Returned None (no objects)")
                        self.set_module_done(name, True)
                    except Exception as e:
                        logger.error(f"[{name}] Failed: {e}", exc_info=True)
                        self.set_module_done(name, False)
                        all_done = False
                    continue

                if kind == "builtin" and name == "classification_api":
                    if not obj_names:
                        logger.warning(f"[{name}] No objects to classify (obj_names empty)")
                        self.set_module_done(name, True)
                        continue
                    results = self._run_snid_classification(self.current_night, obj_names, spectrum_map, logger)
                    logger.info(f"[{name}] SNID classified {len(results)} objects")
                    self.set_module_done(name, True)
                    continue

                # ...rest of loop unchanged...

            for step in self.steps:
                # Placeholder for additional step handling retained
                pass
            # After processing all steps:
            if all_done:
                logger.info("All steps completed; exiting manager loop.")
                # Notify Slack on pipeline completion
                try:
                    send_slack_message(
                        text=f":white_check_mark: Pipeline complete for night={self.current_night or night} (env={env}). Data ready for inspection."
                    )
                except Exception:
                    logger.debug("Slack notification failed (completion).", exc_info=True)
                break
            if all(self.check_module_done(s["name"]) for s in self.steps):
                logger.info("All steps reported DONE; stopping loop.")
                try:
                    send_slack_message(
                        text=f":white_check_mark: Pipeline complete for night={self.current_night or night} (env={env}). Data ready for inspection."
                    )
                except Exception:
                    logger.debug("Slack notification failed (reported DONE).", exc_info=True)
                break
            if one_shot:
                logger.info("one_shot=True; stopping after single iteration.")
                break
            time.sleep(sleep_seconds)

    def list_spectra_objects(self, night: str) -> list[int]:
        """
        List tides_ids from spectra files in /data/spectra/<night>.
        Accepts files like <id>_spectrum.txt or <id>.txt.
        """
        d = util_spectra_night_dir(self.config, str(night), ensure=False)
        ids: list[int] = []
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if not fn.endswith(".txt"):
                    continue
                name = fn[:-4]  # strip .txt
                if name.endswith("_spectrum"):
                    name = name[:-9]
                try:
                    ids.append(int(name))
                except ValueError:
                    continue
        return sorted(set(ids))

    def classify_night(self, night: str, objects: Optional[list[int]] = None, logger: Optional[logging.Logger] = None) -> dict:
        """
        Run SNID classification only for the given night.
        If objects is None, derive from spectra directory.
        """
        self.current_night = str(night)
        lg = logger or setup_logger(night, self.config)
        lg.info(f"[classify_night] Starting SNID-only classification for night={night}")

        # Build spectrum_map by querying DB for existing spectra
        conn = self._get_db_connection()
        obj_names = [str(o) for o in (objects or self.list_spectra_objects(night))]
        spectrum_map = {}
        
        if conn and obj_names:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT tides_id, tides_specid, filepath
                        FROM tides_spec
                        WHERE tides_id = ANY(%s)
                    """, ([int(o) for o in obj_names],))
                    for row in cur.fetchall():
                        tid, spec_id, fpath = row
                        spectrum_map[str(tid)] = {'tides_specid': spec_id, 'filepath': fpath}
            except Exception as e:
                lg.warning(f"[classify_night] Failed to load spectrum map: {e}")
        
        if not spectrum_map:
            lg.warning(f"[classify_night] No spectra found for night={night}")
            return {"night": night, "count": 0, "results": []}

        results = self._run_snid_classification(self.current_night, obj_names, spectrum_map, lg)
        lg.info(f"[classify_night] SNID finished for {len(results)} objects")
        self.set_module_done("classification_api", True)
        return {"night": night, "count": len(results), "results": results}

