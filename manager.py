# Core management class
# tides_pipe/manager.py
import importlib
import os
import yaml
import logging
import time
import pathlib
import datetime
import signal
import argparse
from typing import List, Optional
import asyncio
from tides_pipe.modules.classifiers.client import classify_snid_async, classify_ngsf_async
from tides_pipe.modules import db as dbutil
from tides_pipe.modules.classifiers.classification_store import save_result
from importlib import import_module
from tides_pipe.modules import status_store

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

    # Resolve log_dir: ENV > config > ./logs/{night}
    env_log_dir = os.getenv("LOG_DIR")
    log_dir = env_log_dir or config.get('log_dir')
    if not log_dir:
        log_dir = os.path.join(os.getcwd(), "logs", str(night))

    pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = os.path.join(log_dir, f"{night or 'run'}.log")

    logger = logging.getLogger("tides_manager")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level)

    # File handler
    fh = logging.FileHandler(log_file)
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)

    # Stdout handler for container logs
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

        # BASE_DIR env override
        base_dir_env = os.getenv("BASE_DIR")
        if base_dir_env:
            self.config['base_dir'] = base_dir_env

        # Ensure base_dir exists
        if 'base_dir' not in self.config or not self.config['base_dir']:
            today = datetime.datetime.now().strftime('%Y%m%d')
            default_dir = os.path.join(os.getcwd(), f"tides_pipe_run_{today}")
            pathlib.Path(default_dir).mkdir(parents=True, exist_ok=True)
            self.config['base_dir'] = default_dir
        else:
            pathlib.Path(self.config['base_dir']).mkdir(parents=True, exist_ok=True)

        # Load pluggable steps (modules or callables). Falls back to legacy modules list.
        self.steps = self.load_steps(modules)
        # Status tracking
        self.current_night: str = ""
        self._status_conn = None

    def load_config(self):
        if not os.path.exists(self.config_path):
            # Minimal default to keep running in Docker if config is mounted later
            return {"modules": [], "base_dir": ""}
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

    def _spectrum_path(self, tides_id: str | int) -> str:
        """
        Resolve spectrum path for a tides_id using config.paths.spectra_dir or base_dir/spectra.
        """
        paths = self.config.get("paths", {})
        spectra_dir = paths.get("spectra_dir") or os.path.join(self.config['base_dir'], "spectra")
        os.makedirs(spectra_dir, exist_ok=True)
        return os.path.join(spectra_dir, f"{tides_id}_spectrum.txt")

    def set_module_done(self, module_name: str, status: bool):
        """
        Record DONE/FAILED for a pseudo-module (e.g., classification_api) to integrate with check_module_done().
        """
        module_done_file = os.path.join(self.config['base_dir'], f"{module_name}_DONE.txt")
        with open(module_done_file, 'w') as f:
            f.write("TRUE\n" if status else "FALSE\n")

    async def _run_classifiers_api(self, obj_names: list[str | int], logger: logging.Logger, snid_params: dict | None = None, ngsf_params: dict | None = None):
        """
        Call classifier microservices (SNID/NGSF) via HTTP for each tides_id concurrently,
        then persist results to the remote DB.
        """
        # Open DB connection once
        conn = None
        try:
            creds = dbutil.load_creds(self.config)
            conn = dbutil.connect(creds)
        except Exception as e:
            logger.error(f"DB connection failed; will skip persistence: {e}")

        async def tagged(method: str, tides_id: int, coro):
            res = await coro
            return method, tides_id, res

        tasks = []
        for tid in obj_names or []:
            spath = self._spectrum_path(tid)
            if not os.path.exists(spath):
                logger.warning(f"Spectrum not found for {tid}: {spath}")
                continue
            tid_int = int(tid)
            tasks.append(tagged("snid", tid_int, classify_snid_async(tid_int, spath, snid_params or {})))
            tasks.append(tagged("ngsf", tid_int, classify_ngsf_async(tid_int, spath, ngsf_params or {})))

        classified_ok = 0
        ok = True
        async for coro in asyncio.as_completed(tasks):
            try:
                method, tid_int, res = await coro
                logger.info(f"{method} result for {tid_int}: {res}")
                if conn:
                    save_result(conn, tid_int, method, res, logger=logger)
                classified_ok += 1
            except Exception as e:
                ok = False
                logger.error(f"Classifier call failed: {e}", exc_info=True)
        
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

    def run(self, night=None, objects=None, one_shot: bool = False, sleep_seconds: int = 60):
        logger = setup_logger(night, self.config)
        logger.info(f"Pipeline starting (night={night}, one_shot={one_shot}, sleep={sleep_seconds}s)")
        global _STOP
        obj_names = []  # carry-forward objects between steps
        # Night context for status/events
        self.current_night = str(night or "")
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
                name, kind, runner, params = step["name"], step["kind"], step["runner"], step.get("params") or {}
                logger.info(f"Running step: {name} ({kind})")
                # Pre-step status/event
                if self._status_conn and self.current_night:
                    try:
                        status_store.add_event(self._status_conn, self.current_night, name, "INFO", "Starting")
                        if name == "data_ingestion":
                            status_store.upsert_status(self._status_conn, self.current_night, "ingesting", message="Ingestion running")
                        elif name.startswith("classification"):
                            status_store.upsert_status(self._status_conn, self.current_night, "classifying", message="Classification running")
                    except Exception:
                        pass
                try:
                    if kind == "module":
                        # Standard module signature
                        out = runner(night, logger, self.config)
                        if isinstance(out, list):
                            obj_names = out
                            # Post-ingestion status: record count
                            if self._status_conn and self.current_night and name == "data_ingestion":
                                try:
                                    status_store.upsert_status(
                                        self._status_conn,
                                        self.current_night,
                                        "ingesting",
                                        ingested=len(obj_names),
                                        message="Ingestion complete",
                                    )
                                except Exception:
                                    pass
                    elif kind == "callable":
                        # Generic callable signature; pass context (kwargs)
                        out = runner(night=night, logger=logger, config=self.config, objects=obj_names, **params)
                        # Allow callables to update objects or return a results file
                        if isinstance(out, dict):
                            obj_names = out.get("objects", obj_names)
                            classification_results_file = out.get("results_file", classification_results_file)
                    elif kind == "builtin" and name == "classification_api":
                        asyncio.run(self._run_classifiers_api(obj_names, logger))
                    else:
                        logger.warning(f"Unknown step kind '{kind}' for {name}")

                    if not self.check_module_done(name):
                        all_done = False
                except Exception as e:
                    logger.error(f"Error in step '{name}': {e}", exc_info=True)
                    if self._status_conn and self.current_night:
                        try:
                            status_store.add_event(self._status_conn, self.current_night, name, "ERROR", str(e))
                            status_store.upsert_status(self._status_conn, self.current_night, "error", message=f"{name} failed")
                        except Exception:
                            pass
                    all_done = False

            if all_done:
                self.send_to_db(classification_results_file, obj_names, logger)
                self.signal_pipeline_done(logger)
                if self._status_conn and self.current_night:
                    try:
                        status_store.upsert_status(self._status_conn, self.current_night, "done", message="Pipeline complete", finished=True)
                        status_store.add_event(self._status_conn, self.current_night, "manager", "INFO", "Pipeline complete")
                    except Exception:
                        pass
            else:
                self.clear_pipeline_done_signal(logger)

            if one_shot:
                logger.info("One-shot mode enabled; exiting after one iteration.")
                if self._status_conn and self.current_night:
                    try:
                        status_store.add_event(self._status_conn, self.current_night, "manager", "INFO", "One-shot exit")
                   

