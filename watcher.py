import os, time, re, httpx
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

PATTERN = re.compile(r".*\.fits$", re.IGNORECASE)
DELIVERIES_DIR = os.getenv("DELIVERIES_DIR", "/data/deliveries")
PIPELINE_API = os.getenv("PIPELINE_API", "http://pipeline:8001")

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

    def flush(self):
        now = time.time()
        ready = [n for n, t in list(self._pending.items()) if now - t > 3.0]
        for night in ready:
            try:
                r = httpx.post(f"{PIPELINE_API}/ingest", json={"night": night}, timeout=30)
                r.raise_for_status()
                print(f"Triggered ingestion for night {night}: {r.json()}")
            except Exception as e:
                print(f"Failed to trigger ingestion for night {night}: {e}")
            finally:
                self._pending.pop(night, None)

def main():
    os.makedirs(DELIVERIES_DIR, exist_ok=True)
    handler = MECHandler()
    obs = Observer()
    obs.schedule(handler, DELIVERIES_DIR, recursive=True)
    obs.start()
    try:
        while True:
            time.sleep(2)
            handler.flush()
    except KeyboardInterrupt:
        obs.stop()
    obs.join()

if __name__ == "__main__":
    main()