# Core management class
# tides_pipe/manager.py
import importlib
import os
import yaml
import logging
import time
import pathlib
import datetime

def setup_logger(night, config):
    # Check config for log_dir, else use default ./logs/{night}
    log_dir = config.get('log_dir')
    if not log_dir:
        # Use current working directory
        log_dir = os.path.join(os.getcwd(), "logs", str(night))
    log_file = os.path.join(log_dir, f"{night}.log")
    if not os.path.exists(log_dir):
        pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    return logging.getLogger()

class PipelineManager:
    def __init__(self, modules=None, config_path=None):
        self.config_path = config_path or "config/config.yml"
        self.config = self.load_config()
        # Ensure base_dir is set
        if 'base_dir' not in self.config or not self.config['base_dir']:
            today = datetime.datetime.now().strftime('%Y%m%d')
            default_dir = os.path.join(os.getcwd(), f"tides_pipe_run_{today}")
            if not os.path.exists(default_dir):
                pathlib.Path(default_dir).mkdir(parents=True, exist_ok=True)
            self.config['base_dir'] = default_dir
        self.modules = self.load_modules(modules)

    def load_config(self):
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def load_modules(self, modules):
        config_modules = self.config.get("modules", [])
        if modules is None:
            modules = config_modules
        else:
            modules = list(set(modules + config_modules))
        
        loaded_modules = [(name, importlib.import_module(f"tides_pipe.modules.{name}").run) for name in modules]
        return loaded_modules

    def run(self, night=None, objects=None):
        logger = setup_logger(night, self.config)
        while True:
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
                    else:
                        module(logger=logger, config=self.config)
                    if not self.check_module_done(name):
                        all_done = False
                except Exception as e:
                    logger.error(f"Error in {name}: {e}")
                    all_done = False

            if all_done:
                self.send_to_db(classification_results_file, obj_names, logger)
                self.signal_pipeline_done(logger)
            else:
                self.clear_pipeline_done_signal(logger)

            time.sleep(60)  # Adjust based on required processing frequency

    def check_module_done(self, module_name):
        module_done_file = os.path.join(self.config['base_dir'], f"{module_name}_DONE.txt")
        if os.path.exists(module_done_file):
            with open(module_done_file, 'r') as f:
                status = f.read().strip()
                return status == "TRUE"
        return False

    def send_to_db(self, results_file, obj_names, logger):
        # Placeholder for sending results to the database
        logger.info(f"Sending results to the database: {results_file}")
        logger.info(f"Loading targets into TOM database: {obj_names}")
        # Execute the management scripts in tidestom
        if results_file:
            os.system(f"python manage.py add_spectra_to_db --pipeline --pipeline-results {results_file}")
        else:
            logger.error("No classification results file provided")

    def signal_pipeline_done(self, logger):
        with open(os.path.join(self.config['base_dir'], "DONE.txt"), 'w') as f:
            f.write("TRUE\n")
        logger.info("Pipeline processing complete. Signaled with DONE.txt")

    def clear_pipeline_done_signal(self, logger):
        done_file = os.path.join(self.config['base_dir'], "DONE.txt")
        if os.path.exists(done_file):
            os.remove(done_file)
        logger.info("Pipeline processing not complete. Cleared DONE.txt signal")


