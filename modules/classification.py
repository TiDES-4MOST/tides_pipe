import os
import logging
import yaml
import importlib
import pandas as pd
import random
from .module import Module
from . import db



class Classifier(Module):
    def __init__(self, code_config, config):
        super().__init__(config)
        self.code = code_config.get("code")
        self.config_file = code_config.get("config_file")
        self.handler = self.load_handler(self.code)

    def load_handler(self, code):
        # Dynamically load the handler for the specified classification code
        module = importlib.import_module(f"tides_pipe.modules.classifiers.{code}_handler.py")
        return module.ClassificationHandler(self.config_file)

    def classify(self, spectrum_path):
        if not os.path.exists(spectrum_path):
            self.logger.error(f"Spectrum file {spectrum_path} not found.")
            return None

        self.logger.info(f"Classifying spectrum: {spectrum_path} using {self.code}")
        result = self.handler.classify(spectrum_path)
        self.logger.info(f"Classification complete for: {spectrum_path} using {self.code}")
        return result

def run(night=None, objects=None, logger=None, config=None):
    classification_config = config.get("classification", {})
    data_paths_config = config.get("data_paths", {})  # Load the data_paths block
    test_mode = classification_config.get("test", False)  # Read test mode from config

    codes = classification_config.get("codes", [])
    classifiers = [Classifier(code_config, classification_config) for code_config in codes]

    for classifier in classifiers:
        classifier.set_logger(logger)

    results = []
    if night:
        spectra_night_dir = os.path.join(data_paths_config.get("spectra_dir", ""), night)  # Use spectra_dir from data_paths
        if not os.path.exists(spectra_night_dir):
            logger.error(f"No spectra found for night {night}.")
            return

        if objects:
            for obj in objects:
                spectrum_path = os.path.join(spectra_night_dir, obj)
                result = {"obj_name": obj}
                if test_mode:
                    # Generate random test classifications
                    result.update({
                        "auto_class_test": random.choice(["Type Ia", "Type Ib", "Type Ic", "Type II"]),
                        "auto_class_subclass_test": random.choice(["Normal", "Broad-lined", "91bg-like"]),
                        "auto_class_prob_test": round(random.uniform(0.5, 1.0), 2)
                    })
                else:
                    for classifier in classifiers:
                        classification_result = classifier.classify(spectrum_path)
                        if classification_result:
                            result.update({
                                f"auto_class_{classifier.code}": classification_result.get("result"),
                                f"auto_class_subclass_{classifier.code}": classification_result.get("subclass"),
                                f"auto_class_prob_{classifier.code}": classification_result.get("probability")
                            })
                results.append(result)
        else:
            for spectrum_file in os.listdir(spectra_night_dir):
                if spectrum_file.endswith(".txt"):
                    spectrum_path = os.path.join(spectra_night_dir, spectrum_file)
                    result = {"obj_name": spectrum_file}
                    if test_mode:
                        # Generate random test classifications
                        result.update({
                            "auto_class_test": random.choice(["Type Ia", "Type Ib", "Type Ic", "Type II"]),
                            "auto_class_subclass_test": random.choice(["Normal", "Broad-lined", "91bg-like"]),
                            "auto_class_prob_test": round(random.uniform(0.5, 1.0), 2)
                        })
                    else:
                        for classifier in classifiers:
                            classification_result = classifier.classify(spectrum_path)
                            if classification_result:
                                result.update({
                                    f"auto_class_{classifier.code}": classification_result.get("result"),
                                    f"auto_class_subclass_{classifier.code}": classification_result.get("subclass"),
                                    f"auto_class_prob_{classifier.code}": classification_result.get("probability")
                                })
                    results.append(result)
    else:
        logger.error("No night specified for classification.")
        return None

    # Save results to pipeline_classification_global table
    try:
        # Load database credentials and connect using db.py
        creds = db.load_creds(config)
        tides_db_conn = db.connect(creds)
    except Exception as e:
        logger.error(f"Failed to connect to database for saving classifications: {e}")
        return None

    if not tides_db_conn:
        logger.error("Failed to connect to database for saving classifications.")
        return None

    try:
        cursor = tides_db_conn.cursor()
        for result in results:
            cursor.execute("""
                INSERT INTO pipeline_classification_global (tides_id, sn_type, subclass, probability, version, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tides_id) DO UPDATE SET
                    sn_type = EXCLUDED.sn_type,
                    subclass = EXCLUDED.subclass,
                    probability = EXCLUDED.probability,
                    version = EXCLUDED.version,
                    notes = EXCLUDED.notes
            """, (
                result.get("obj_name"),  # tides_id (use obj_name for testing)
                result.get("auto_class_test", "Unknown"),  # sn_type
                result.get("auto_class_subclass_test", "Unknown"),  # subclass
                result.get("auto_class_prob_test", 0.0),  # probability
                "test_version",  # version
                "Generated by test mode"  # notes
            ))
        tides_db_conn.commit()
        logger.info("Test classifications saved to pipeline_classification_global.")
    except Exception as e:
        logger.error(f"Failed to save test classifications: {e}")
    finally:
        tides_db_conn.close()

    results_df = pd.DataFrame(results)
    results_file = os.path.join(data_paths_config.get("spectra_dir", ""), f"{night}_classification_results.csv")  # Save results in spectra_dir
    results_df.to_csv(results_file, index=False)
    logger.info(f"Classification results saved to {results_file}")
    return results_file