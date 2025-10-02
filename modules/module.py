# filepath: tides_pipe/modules/module.py
import logging
import os
from . import db

class Module:
    def __init__(self, config):
        self.logger = logging.getLogger()
        self.config = config
        self.done = False

    def set_logger(self, logger):
        self.logger = logger

    def set_done(self, status):
        self.done = status
        if self.done:
            self.write_done_file()

    def write_done_file(self):
        done_file = os.path.join(self.config['base_dir'], f"{self.__class__.__name__.lower()}_DONE.txt")
        with open(done_file, 'w') as f:
            f.write("TRUE\n")
        self.logger.info(f"{self.__class__.__name__} processing complete. Signaled with DONE.txt")

    def connect_to_db(self, db_name=None):
        """Connect to a PostgreSQL database using db.py configuration."""
        try:
            # Load database credentials using db.py
            creds = db.load_creds(self.config)
            
            # If a specific db_name is provided, override the default
            if db_name:
                creds = creds.copy()  # Don't modify the original
                creds["name"] = db_name
                
            # Connect using the db.py connect function
            conn = db.connect(creds)
            return conn
        except Exception as e:
            db_name_str = db_name or creds.get("name", "unknown") if 'creds' in locals() else "unknown"
            self.logger.error(f"Failed to connect to database {db_name_str}: {e}")
            return None