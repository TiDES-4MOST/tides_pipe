import os, time, re, httpx, sys, logging
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

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
    # New structure: <env>/<night>/...
    # parts[0] is env, parts[1] is night
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[0], parts[1]
    return None, None

class MECHandler(FileSystemEventHandler):
    def __init__(self):
        # Key is now (env, night) tuple
        self._pending = {}

    def on_created(self, event):
        if not event.is_directory and PATTERN.match(event.src_path):
            logger.info(f"File created: {event.src_path}")
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and PATTERN.match(event.src_path):
            logger.info(f"File modified: {event.src_path}")
            self._schedule(event.src_path)

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