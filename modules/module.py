# filepath: tides_pipe/modules/module.py
import logging
import os

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

    def connect_to_db(self, db_name):
        """Connect to a PostgreSQL database."""
        try:
            conn = psycopg2.connect(
                dbname=db_name,
                user=self.config["db_creds"]["user"],
                password=self.config["db_creds"]["password"],
                host=self.config["db_creds"]["host"],
                port=self.config["db_creds"]["port"],
            )
            return conn
        except Exception as e:
            self.logger.error(f"Failed to connect to database {db_name}: {e}")
            return None