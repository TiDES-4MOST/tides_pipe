# filepath: /Users/pwise/4MOST/tides/tides_pipe/classification_handlers/example_classification_code.py
import importlib
import yaml

class ClassificationHandler:
    def __init__(self, handler_name, config_file):
        self.config_file = config_file
        with open(config_file, 'r') as f:
            self.config = yaml.safe_load(f)
        # Dynamically import the handler module
        module_path = f"tides_pipe.modules.classifiers.{handler_name}_handler"
        handler_module = importlib.import_module(module_path)
        # Instantiate the handler class (must be named Handler in each module)
        self.handler = handler_module.Handler(self.config)

    def classify(self, spectrum_path):
        # Delegate to the specific handler
        return self.handler.classify(spectrum_path)