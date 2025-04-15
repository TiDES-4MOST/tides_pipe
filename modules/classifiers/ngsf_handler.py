# filepath: /Users/pwise/4MOST/tides/tides_pipe/classification_handlers/ngsf.py
import logging
import yaml
from pathlib import Path
from classification_handlers import ClassificationHandler
from NGSF.NGSF.sf_class import *
from NGSF.NGSF.params import Parameters
from classifiers.NGSF.run_ngsf import create_parameters_file, create_run_file, create_temp_files, run_ngsf


class NGSFHandler(ClassificationHandler):
    def __init__(self, config_file):
        super().__init__(config_file)
        self.config = self.load_config(config_file)

    def load_config(self, config_file):
        """
        Load the YAML configuration file.
        """
        with open(config_file, "r") as file:
            return yaml.safe_load(file)

    def classify(self, spectrum_path):
        """
        Run the NGSF classification using the configuration file and spectrum path.
        """
        logging.info(f"Running NGSF classification on {spectrum_path} using {self.config_file}")

        # Populate arguments from the configuration file
        args = {
            "bank_dir": self.config.get("bank_dir", "default_bank_dir"),
            "output_dir": self.config.get("output_dir", "default_output_dir"),
            "Alam_high": self.config.get("Alam_high", 2.0),
            "Alam_low": self.config.get("Alam_low", -2.0),
            "Alam_interval": self.config.get("Alam_interval", 0.2),
            "epoch_low": self.config.get("epoch_low", 0),
        }

        # Call the functions directly
        create_parameters_file(args)
        create_run_file()
        create_temp_files()
        run_ngsf()

        # Return a dummy result for demonstration purposes
        return {"result": "ngsf_classified", "spectrum": spectrum_path}