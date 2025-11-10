import os, time, re, httpx, sys, logging
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

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

def night_from_path(path: str) -> str | None:
    rel = os.path.relpath(path, DELIVERIES_DIR)
    parts = rel.split(os.sep)
    return parts[0] if parts and parts[0].isdigit() else None

class MECHandler(FileSystemEventHandler):
    def __init__(self):
        self._pending = {}

    def on_created(self, event):
        if not event.is_directory and PATTERN.match(event.src_path):
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and PATTERN.match(event.src_path):
            self._schedule(event.src_path)

    def _schedule(self, path: str):
        n = night_from_path(path)
        if n:
            self._pending[n] = time.time()
            logging.debug("Scheduled night %s from path %s", n, path)

    def flush(self):
        now = time.time()
        ready = [n for n, t in list(self._pending.items()) if now - t > DEBOUNCE_SEC]
        for night in ready:
            try:
                logging.info("Triggering ingestion for night %s", night)
                r = httpx.post(f"{PIPELINE_API}/ingest", json={"night": night}, timeout=30)
                r.raise_for_status()
                logging.info("Ingestion triggered for night %s: %s", night, r.json())
            except Exception as e:
                logging.exception("Failed to trigger ingestion for night %s: %s", night, e)
            finally:
                self._pending.pop(night, None)

def main():
    setup_logging()
    os.makedirs(DELIVERIES_DIR, exist_ok=True)
    observer_cls = PollingObserver if USE_POLLING else Observer
    handler = MECHandler()
    obs = observer_cls()
    obs.schedule(handler, DELIVERIES_DIR, recursive=True)
    obs.start()
    logging.info(
        "Watcher started. dir=%s api=%s observer=%s debounce=%.1fs",
        DELIVERIES_DIR, PIPELINE_API, obs.__class__.__name__, DEBOUNCE_SEC
    )
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