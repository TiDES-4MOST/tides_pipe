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

        self.modules = self.load_modules(modules)

    def load_config(self):
        if not os.path.exists(self.config_path):
            # Minimal default to keep running in Docker if config is mounted later
            return {"modules": [], "base_dir": ""}
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f) or {"modules": []}

    def load_modules(self, modules):
        config_modules = self.config.get("modules", [])
        if modules is None:
            modules = config_modules
        else:
            # preserve order, de-dupe
            seen = set()
            merged = []
            for m in modules + config_modules:
                if m not in seen:
                    merged.append(m)
                    seen.add(m)
            modules = merged

        loaded_modules = []
        for name in modules:
            # Pseudo-modules handled inside manager.run
            if name in ("classification_api",):
                loaded_modules.append((name, None))
                continue
            try:
                mod = importlib.import_module(f"tides_pipe.modules.{name}")
                run_func = getattr(mod, "run")
                loaded_modules.append((name, run_func))
            except Exception as e:
                # Log and skip unknown/broken modules
                logging.getLogger("tides_manager").error(f"Failed to load module '{name}': {e}", exc_info=True)
        return loaded_modules

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
        Call classifier microservices (SNID/NGSF) via HTTP for each tides_id concurrently.
        Service URLs/endpoints are configured via env in the classifier client.
        """
        tasks = []
        for tid in obj_names or []:
            spath = self._spectrum_path(tid)
            if not os.path.exists(spath):
                logger.warning(f"Spectrum not found for {tid}: {spath}")
                continue
            # Fan out concurrent calls
            tasks.append(classify_snid_async(int(tid), spath, snid_params or {}))
            tasks.append(classify_ngsf_async(int(tid), spath, ngsf_params or {}))

        ok = True
        for coro in asyncio.as_completed(tasks):
            try:
                res = await coro
                logger.info(f"Classifier result: {res}")
            except Exception as e:
                ok = False
                logger.error(f"Classifier call failed: {e}", exc_info=True)

        # Mark as done so check_module_done('classification_api') passes
        self.set_module_done("classification_api", ok)

    def run(self, night=None, objects=None, one_shot: bool = False, sleep_seconds: int = 60):
        logger = setup_logger(night, self.config)
        logger.info(f"Pipeline starting (night={night}, one_shot={one_shot}, sleep={sleep_seconds}s)")
        global _STOP

        while True:
            if _STOP:
                logger.info("Shutdown signal received; exiting manager loop.")
                break

            all_done = True
            classification_results_file = None
            obj_names = []

            for name, module in self.modules:
                logger.info(f"Running module: {name}")
                try:
                    if name == "data_ingestion":
                        obj_names = module(night, logger, self.config)
                    elif name == "classification":
                        classification_results_file = module(night=night, objects=obj_names, logger=logger, config=self.config)
                    elif name == "classification_api":
                        # Call classifier microservices over HTTP
                        asyncio.run(self._run_classifiers_api(obj_names, logger))
                    else:
                        module(logger=logger, config=self.config)

                    if not self.check_module_done(name):
                        all_done = False
                except Exception as e:
                    logger.error(f"Error in {name}: {e}", exc_info=True)
                    all_done = False

            if all_done:
                self.send_to_db(classification_results_file, obj_names, logger)
                self.signal_pipeline_done(logger)
            else:
                self.clear_pipeline_done_signal(logger)

            if one_shot:
                logger.info("One-shot mode enabled; exiting after one iteration.")
                break

            time.sleep(sleep_seconds)

    def check_module_done(self, module_name):
        module_done_file = os.path.join(self.config['base_dir'], f"{module_name}_DONE.txt")
        if os.path.exists(module_done_file):
            with open(module_done_file, 'r') as f:
                status = f.read().strip()
                return status == "TRUE"
        return False


    def signal_pipeline_done(self, logger):
        with open(os.path.join(self.config['base_dir'], "DONE.txt"), 'w') as f:
            f.write("TRUE\n")
        logger.info("Pipeline processing complete. Signaled with DONE.txt")

    def clear_pipeline_done_signal(self, logger):
        done_file = os.path.join(self.config['base_dir'], "DONE.txt")
        if os.path.exists(done_file):
            os.remove(done_file)
        logger.info("Pipeline processing not complete. Cleared DONE.txt signal")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TiDES Pipeline Manager")
    parser.add_argument("--night", help="Night to process (e.g. 20250129)")
    parser.add_argument("--modules", nargs="*", help="Override module list (defaults to config.modules)")
    parser.add_argument("--config", help="Path to config.yml (defaults to $TIDES_CONFIG or config/config.yml)")
    parser.add_argument("--one-shot", action="store_true", help="Run once then exit (or set ONE_SHOT=1)")
    parser.add_argument("--sleep", type=int, default=int(os.getenv("LOOP_SLEEP", "60")), help="Sleep seconds between loops")
    args = parser.parse_args()

    mgr = PipelineManager(modules=args.modules, config_path=args.config)
    one_shot_env = os.getenv("ONE_SHOT", "").lower() in ("1", "true", "yes")
    mgr.run(night=args.night, one_shot=(args.one_shot or one_shot_env), sleep_seconds=args.sleep)


