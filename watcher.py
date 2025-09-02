import os, time, re, docker, pathlib
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

PATTERN = re.compile(r".*\.fits$", re.IGNORECASE)
DELIVERIES_DIR = os.getenv("DELIVERIES_DIR", "/data/deliveries")

# Container launch settings
PIPELINE_IMAGE = os.getenv("PIPELINE_IMAGE", "tides_pipeline")  # image or service image name
PIPELINE_NETWORK = os.getenv("PIPELINE_NETWORK", None)          # e.g. tides_pipe_default (set via COMPOSE_PROJECT_NAME)
HOST_DELIVERIES_DIR = os.getenv("HOST_DELIVERIES_DIR", None)    # absolute host path to deliveries
HOST_SPECTRA_DIR = os.getenv("HOST_SPECTRA_DIR", None)          # absolute host path to spectra
HOST_PLOTS_DIR = os.getenv("HOST_PLOTS_DIR", None)              # absolute host path to static plots

def night_from_path(path: str) -> str | None:
    try:
        rel = os.path.relpath(path, DELIVERIES_DIR)
    except ValueError:
        return None
    parts = rel.split(os.sep)
    return parts[0] if parts and parts[0].isdigit() else None

class MECHandler(FileSystemEventHandler):
    def __init__(self):
        self._pending = {}
        self.client = docker.from_env()

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
            self._start_manager(night)
            self._pending.pop(night, None)

    def _start_manager(self, night: string):
        print(f"Starting manager for night {night}")
        binds = {}
        if HOST_DELIVERIES_DIR:
            binds[HOST_DELIVERIES_DIR] = {"bind": "/data/deliveries", "mode": "rw"}
        if HOST_SPECTRA_DIR:
            binds[HOST_SPECTRA_DIR] = {"bind": "/data/spectra", "mode": "rw"}
        if HOST_PLOTS_DIR:
            binds[HOST_PLOTS_DIR] = {"bind": "/data/static/plots", "mode": "rw"}

        env = {
            "TIDES_CONFIG": os.getenv("TIDES_CONFIG", "/app/tides_pipe/config/config.yml"),
            "DELIVERIES_DIR": "/data/deliveries",
            "SPECTRA_DIR": "/data/spectra",
            "STATIC_PLOTS_DIR": "/data/static/plots",
            # DB credentials forwarded from this container's env
            "DB_HOST": os.getenv("DB_HOST", ""),
            "DB_PORT": os.getenv("DB_PORT", "5432"),
            "DB_NAME": os.getenv("DB_NAME", ""),
            "DB_USER": os.getenv("DB_USER", ""),
            "DB_PASSWORD": os.getenv("DB_PASSWORD", ""),
            "DB_SSLMODE": os.getenv("DB_SSLMODE", "prefer"),
            # classifier endpoints (compose service DNS)
            "CLASSIFIER_SNID_URL": os.getenv("CLASSIFIER_SNID_URL", "http://snid_api:8000"),
            "CLASSIFIER_NGSF_URL": os.getenv("CLASSIFIER_NGSF_URL", "http://ngsf_api:8000"),
        }

        kwargs = dict(
            image=PIPELINE_IMAGE,
            command=["python", "/app/tides_pipe/manager.py", "--night", night],
            environment=env,
            volumes=binds,
            detach=True,
        )
        if PIPELINE_NETWORK:
            kwargs["network"] = PIPELINE_NETWORK

        try:
            cont = self.client.containers.run(**kwargs)
            print(f"Manager container started: {cont.short_id}")
        except Exception as e:
            print(f"Failed to start manager: {e}")

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