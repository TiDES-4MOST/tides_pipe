# Core management class
# tides_pipe/manager.py
import importlib
import os
import logging
import signal
from typing import List, Optional

from tides_pipe.modules.classifiers.snid_handler import SnidHandler
from tides_pipe.utils.paths import spectra_night_dir as util_spectra_night_dir, spectrum_path as util_spectrum_path

_STOP = False

def _handle_sigterm(signum, frame):
    global _STOP
    _STOP = True

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)

def setup_logger(night, config):
    """
    File + stdout logger so `docker logs` shows output.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)

    # Resolve log_dir: ENV > structured data path
    env_log_dir = os.getenv("LOG_DIR")
    if env_log_dir:
        log_dir = env_log_dir
    else:
        # Use structured data path: /data/logs/{night}
        data_paths = config.get("data_paths", {})
        spectra_dir = data_paths.get("spectra_dir", "/data/spectra")
        base_data_dir = os.path.dirname(spectra_dir)  # /data
        log_dir = os.path.join(base_data_dir, "logs")
        if night:
            log_dir = os.path.join(log_dir, str(night))

    pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = os.path.join(log_dir, f"{night or 'run'}.log")

    logger = logging.getLogger("tides_manager")

    # Always set level and stop propagation to root to prevent duplicates
    logger.setLevel(level)
    logger.propagate = False

    # Add handlers only if not already present
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        fh = logging.FileHandler(log_file)
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(level)
        sh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(sh)

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
        self.snid = SnidHandler()

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

    def _logs_dir(self) -> str:
        """Get the logs directory path using data_paths structure."""
        data_paths = self.config.get("data_paths", {})
        # Get base data directory (parent of spectra_dir, deliveries_dir, etc.)
        spectra_dir = data_paths.get("spectra_dir", "/data/spectra")
        base_data_dir = os.path.dirname(spectra_dir)  # /data
        logs_dir = os.path.join(base_data_dir, "logs")
        
        # Include night directory if current_night is set
        if self.current_night:
            logs_dir = os.path.join(logs_dir, self.current_night)
            
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
        # Convert module name to class name format for file lookup
        # e.g., "data_ingestion" -> "dataingestion"
        if module_name == "data_ingestion":
            file_module_name = "dataingestion"
        elif module_name == "classification_api":
            file_module_name = "classification_api"
        else:
            file_module_name = module_name
            
        module_done_file = os.path.join(logs_dir, f"{file_module_name}_DONE.txt")
        if os.path.exists(module_done_file):
            with open(module_done_file, 'r') as f:
                content = f.read().strip()
                return content == "TRUE"
        return False

    async def _run_classifiers_api(self, obj_names: list[str | int], logger: logging.Logger, snid_params: dict | None = None, ngsf_params: dict | None = None):
        """
        Call classifier microservices (SNID/NGSF) via HTTP for each tides_id concurrently,
        then persist results to the remote DB.
        """
        logger.info(f"[classification_api] Starting classification for {len(obj_names or [])} objects")
        
        # Open DB connection once
        conn = None
        try:
            creds = dbutil.load_creds(self.config)
            conn = dbutil.connect(creds)
            logger.info(f"[classification_api] Database connection established")
        except Exception as e:
            logger.error(f"[classification_api] DB connection failed; will skip persistence: {e}")

        async def tagged(method: str, tides_id: int, coro):
            res = await coro
            return method, tides_id, res

        tasks = []
        logger.info(f"[classification_api] Looking for spectra in night directory: {self.current_night}")
        logger.info(f"[classification_api] Config data_paths: {self.config.get('data_paths', {})}")
        
        # Get enabled classifiers from config
        classification_config = self.config.get('classification', {})
        enabled_classifiers = classification_config.get('enabled_classifiers', ['snid', 'ngsf'])
        logger.info(f"[classification_api] Enabled classifiers: {enabled_classifiers}")
        
        for tid in obj_names or []:
            spath = self._spectrum_path(tid)
            if not os.path.exists(spath):
                logger.warning(f"[classification_api] Spectrum not found for {tid}: {spath}")
                continue
            logger.debug(f"[classification_api] Found spectrum for {tid}: {spath}")
            tid_int = int(tid)
            
            # Add tasks for enabled classifiers only
            if 'snid' in enabled_classifiers:
                tasks.append(tagged("snid", tid_int, classify_snid_async(tid_int, spath, snid_params or {})))
            if 'ngsf' in enabled_classifiers:
                tasks.append(tagged("ngsf", tid_int, classify_ngsf_async(tid_int, spath, ngsf_params or {})))
            # TODO: Add qxp when classify_qxp_async is implemented
            # if 'qxp' in enabled_classifiers:
            #     tasks.append(tagged("qxp", tid_int, classify_qxp_async(tid_int, spath, qxp_params or {})))

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
        
        # Update status after classification progress/completion
        if self._status_conn and self.current_night:
            try:
                status_store.upsert_status(
                    self._status_conn,
                    self.current_night,
                    "classifying",
                    classified=classified_ok,
                    message="Classification completed" if ok else "Classification completed with errors",
                )
                status_store.add_event(
                    self._status_conn,
                    self.current_night,
                    "classification_api",
                    "INFO" if ok else "ERROR",
                    f"classified {classified_ok} objects",
                )
            except Exception:
                pass

        self.set_module_done("classification_api", ok)
        if conn:
            try:
                conn.close()
            except Exception:
                pass

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
        cfg = (self.config.get("snid") or {}).get("defaults", {})
        def g(key, default):
            return cfg.get(key, default)

        # Normalize 'use' to a Python list
        use = g("use", ["Ia", "Ib", "Ic", "II", "NotSN"])
        if isinstance(use, str):
            use = [s.strip() for s in use.split(",") if s.strip()]

        return {
            "wmin": float(g("wmin", 4000.0)),
            "wmax": float(g("wmax", 9000.0)),
            "zmin": float(g("zmin", 0.1)),
            "zmax": float(g("zmax", 1.2)),
            "emclip": g("emclip", None),
            "emwid": int(g("emwid", 40)),
            "agemin": int(g("agemin", -90)),
            "agemax": int(g("agemax", 1000)),
            "aband": bool(g("aband", False)),
            "use": use,
        }

    def _run_snid_classification(self, night: str, obj_names: list, logger) -> list[dict]:
        results = []
        params = self._snid_default_params()
        for obj_id in (obj_names or []):
            spath = util_spectrum_path(self.config, night, obj_id, ensure_dir=True)
            try:
                logger.info(f"[classification_api] SNID classify obj={obj_id} path={spath}")
                res = self.snid.classify(spath, night, obj_id, snid_params=params)
                results.append({"obj_id": obj_id, "snid": res})
            except Exception as e:
                logger.exception(f"[classification_api] SNID failed for {obj_id}: {e}")
        return results

    def run(self, night=None, objects=None, one_shot: bool = False, sleep_seconds: int = 60):
        logger = setup_logger(night, self.config)
        self.current_night = str(night or "")
        try:
            sdir = util_spectra_night_dir(self.config, self.current_night, ensure=True)
            logger.info(f"[config] spectra_night_dir={sdir} (real={os.path.realpath(sdir)})")
        except Exception as e:
            logger.error(f"[config] Invalid spectra_night_dir: {e}")
            return

        logger.info(f"Pipeline starting (night={night}, one_shot={one_shot}, sleep={sleep_seconds}s)")
        # Redirect thumbnails to spectra night dir (single source of truth)
        thumb_dir = self._spectra_night_dir()
        self.config.setdefault("data_paths", {})
        self.config["data_paths"]["static_plots_dir"] = thumb_dir
        os.environ["STATIC_PLOTS_DIR"] = thumb_dir
        logger.info(f"[config] Thumbnails will be saved to {thumb_dir}")
        # Open status DB connection (optional; continue if not available)
        try:
            creds = dbutil.load_creds(self.config)
            self._status_conn = dbutil.connect(creds)
            if self.current_night:
                status_store.upsert_status(self._status_conn, self.current_night, "running", message="Manager started")
                status_store.add_event(self._status_conn, self.current_night, "manager", "INFO", "Manager loop starting")
        except Exception as e:
            logger.warning(f"Status DB not available: {e}")

        while True:
            if _STOP:
                logger.info("Shutdown signal received; exiting manager loop.")
                if self._status_conn and self.current_night:
                    try:
                        status_store.add_event(self._status_conn, self.current_night, "manager", "INFO", "Shutdown requested")
                        status_store.upsert_status(self._status_conn, self.current_night, "error", message="Interrupted", finished=True)
                    except Exception:
                        pass
                break

            all_done = True
            classification_results_file = None

            for step in self.steps:
                name = step["name"]
                kind = step.get("kind", "builtin")
                # Skip if already done
                if self.check_module_done(name):
                    logger.info(f"[{name}] Step already completed, skipping")
                    continue

                if kind == "builtin" and name == "classification_api":
                    results = self._run_snid_classification(self.current_night, obj_names or [], logger)
                    logger.info(f"[{name}] SNID finished for {len(results)} objects")
                    self.set_module_done(name, True)
                    continue

                # ...existing code for other steps...

