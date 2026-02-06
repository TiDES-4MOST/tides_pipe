import os, time, re, httpx, sys, logging
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

# Robust import for Slack helper: prefer package path, fallback to local utils, else no-op
try:
    from tides_pipe.utils.slack import send_slack_message
except Exception:
    try:
        from utils.slack import send_slack_message
    except Exception:
        def send_slack_message(text: str, **kwargs):
            return False

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PATTERN = re.compile(r".*\.fits$", re.IGNORECASE)
DELIVERIES_DIR = os.getenv("DELIVERIES_DIR", "/data/deliveries")
PIPELINE_API = os.getenv("PIPELINE_API", "http://pipeline:8001")
DEBOUNCE_SEC = float(os.getenv("DEBOUNCE_SEC", "3.0"))
USE_POLLING = os.getenv("WATCHER_POLLING", "").lower() in ("1", "true", "yes")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def setup_logging():
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
        force=True,
    )
    try:
        sys.stdout.reconfigure(line_buffering=True)  # Python 3.7+
    except Exception:
        pass

def info_from_path(path: str):
    rel = os.path.relpath(path, DELIVERIES_DIR)
    parts = rel.split(os.sep)
    # Expected structure: <env>/<night>/...
    # Accept night in formats: YYYYMMDD, YYYY-MM-DD, YYYY_MM_DD; else fall back to raw folder name
    if len(parts) >= 2:
        env = parts[0]
        night_raw = parts[1]
        night = None
        # Direct digits (YYYYMMDD)
        if night_raw.isdigit():
            night = night_raw
        else:
            m = re.match(r"^(\d{4})[-_]?(\d{2})[-_]?(\d{2})$", night_raw)
            if m:
                night = "".join(m.groups())
        return env, (night or night_raw)
    return None, None

class MECHandler(FileSystemEventHandler):
    def __init__(self):
        # Key is now (env, night) tuple
        self._pending = {}

    def _schedule_dir(self, dir_path: str):
        """Scan a directory tree for .fits files and schedule by (env, night).
        Useful when a new night directory appears via create or move with files pre-populated.
        """
        try:
            if not os.path.isdir(dir_path):
                return
            for root, _, files in os.walk(dir_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if PATTERN.match(fpath):
                        self._schedule(fpath)
        except Exception:
            logging.exception("Failed scanning directory %s", dir_path)

    def on_created(self, event):
        if event.is_directory:
            # New night directory created; scan for existing files
            logger.info(f"Directory created: {event.src_path}")
            self._schedule_dir(event.src_path)
        else:
            if PATTERN.match(event.src_path):
                logger.info(f"File created: {event.src_path}")
                self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and PATTERN.match(event.src_path):
            logger.info(f"File modified: {event.src_path}")
            self._schedule(event.src_path)

    def on_moved(self, event):
        # Handle directories or files moved into deliveries (common for atomic writes)
        try:
            if event.is_directory:
                logger.info(f"Directory moved: {getattr(event, 'dest_path', event.src_path)}")
                self._schedule_dir(getattr(event, 'dest_path', event.src_path))
            else:
                dest = getattr(event, 'dest_path', event.src_path)
                if PATTERN.match(dest):
                    logger.info(f"File moved: {dest}")
                    self._schedule(dest)
        except Exception:
            logging.exception("Error handling move event: %s", event)

    def _schedule(self, path: str):
        env, n = info_from_path(path)
        if env and n:
            key = (env, n)
            logger.info(f"Scheduled env {env} night {n} for file {path}")
            self._pending[key] = time.time()
            logging.debug("Scheduled env %s night %s from path %s", env, n, path)

    def flush(self):
        now = time.time()
        # Key is (env, night)
        ready = [key for key, t in list(self._pending.items()) if now - t > DEBOUNCE_SEC]
        for (env, night) in ready:
            try:
                logging.info("Triggering ingestion for env %s night %s", env, night)
                r = httpx.post(f"{PIPELINE_API}/ingest", json={"env": env, "night": night}, timeout=30)
                r.raise_for_status()
                logging.info("Ingestion triggered for env %s night %s: %s", env, night, r.json())
                # Notify Slack that new data appeared and ingestion was triggered
                send_slack_message(
                    text=f":eyes: New data detected (env={env}, night={night}). Ingestion triggered."
                )
            except Exception as e:
                logging.exception("Failed to trigger ingestion for env %s night %s: %s", env, night, e)
            finally:
                self._pending.pop((env, night), None)

def main():
    setup_logging()
    os.makedirs(DELIVERIES_DIR, exist_ok=True)
    logger.info(f"Starting watcher on {DELIVERIES_DIR}")
    handler = MECHandler()
    observer_cls = PollingObserver if USE_POLLING else Observer  # choose implementation
    logger.info(f"Using {'PollingObserver' if USE_POLLING else 'Observer'} (polling={USE_POLLING})")
    obs = observer_cls()
    obs.schedule(handler, DELIVERIES_DIR, recursive=True)
    obs.start()
    logger.info(f"Watcher started, monitoring {DELIVERIES_DIR} recursively")
    try:
        while True:
            time.sleep(2)
            handler.flush()
    except KeyboardInterrupt:
        logging.info("Stopping watcher...")
        obs.stop()
    obs.join()
    logging.info("Watcher stopped.")

if __name__ == "__main__":
    main()