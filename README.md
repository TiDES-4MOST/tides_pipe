# TiDES Pipe

Backend pipeline for ingesting, processing, and classifying 4MOST TiDES spectra. It supports:
- Watching for new deliveries and triggering processing
- Ingestion into a remote PostgreSQL
- Calling classifier microservices (SNID, NGSF) via FastAPI
- Persisting classification results and pipeline status to the database

This README covers configuration, Docker-based deployment, local development, and operations.

---

## Quick start (Docker Compose)

1) Create a .env (not committed) with your remote DB and classifier URLs:
```env
DB_HOST=db.example.org
DB_PORT=5432
DB_NAME=tides_db
DB_USER=tides_user
DB_PASSWORD=supersecret
DB_SSLMODE=require

# Classifier services (inside compose network or external)
CLASSIFIER_SNID_URL=http://snid_api:8000
CLASSIFIER_NGSF_URL=http://ngsf_api:8000
```

2) Ensure you have the expected directory layout:
```text
tides_pipe/
  deliveries/           # incoming MEC files: deliveries/<night>/...fits
  spectra/              # pipeline-written spectra
  static/plots/         # generated plots (optional)
  config/config.yml     # pipeline YAML config
```

3) Bring up the stack (API + watcher + classifiers):
```bash
cd tides_pipe
docker compose --env-file .env up -d --build
```

What happens:
- watcher monitors ./deliveries and, on new MEC files, POSTs to the pipeline API.
- pipeline API starts the manager for that night in the background.
- manager runs ingestion, calls classifier APIs, and writes results/status to Postgres.

Open the API health:
- http://localhost:8001/health

---

## Architecture

- watcher: monitors a host-mounted deliveries_dir and triggers processing (via pipeline API).
- pipeline API: FastAPI app that accepts POST /ingest and runs manager once per night.
- manager: orchestrates steps loaded from config; calls ingestion (as a module), then classifiers (HTTP APIs).
- classifiers: independent FastAPI microservices for SNID and NGSF (Dockerized).

Notes:
- You can switch to a “job-per-night” pattern (watcher starts one-off manager containers) if you prefer. The default here uses an API trigger.
- TOM remains in a separate container and reads results/status directly from the shared Postgres.

---

## Configuration

The pipeline reads a YAML config and environment variables.

- Default config path: TIDES_CONFIG (env) or config/config.yml
- Recommended config keys:
```yaml
base_dir: "/app/run"
paths:
  deliveries_dir: "/data/deliveries"
  spectra_dir: "/data/spectra"
  archive_dir: "/data/archive"
  static_plots_dir: "/data/static/plots"
log_dir: "/data/logs"

# Legacy modules list (still supported):
modules:
  - data_ingestion
  - classification_api   # pseudo-step handled by manager to call HTTP classifiers

# Or new pluggable steps syntax (optional):
# steps:
#   - type: module
#     import: data_ingestion
#   - type: callable
#     name: classification_api
#     func: tides_pipe.modules.classifiers.api_step:run
#     params:
#       snid_params: {}
#       ngsf_params: {}
```

Environment variables (Compose passes these to containers):
- DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSLMODE
- CLASSIFIER_SNID_URL, CLASSIFIER_NGSF_URL
- CLASSIFIER_SNID_ENDPOINT (default /classify), CLASSIFIER_NGSF_ENDPOINT (default /ngsf_params/)
- DELIVERIES_DIR, SPECTRA_DIR, STATIC_PLOTS_DIR (override config.paths)
- LOG_DIR, BASE_DIR, TIDES_CONFIG
- ONE_SHOT=1 (for job-mode), LOG_LEVEL=INFO|DEBUG

---

## Database

TiDES Pipe expects a remote PostgreSQL. Provide credentials via env.

Optional helper tables to track pipeline status and events:
```sql
CREATE TABLE IF NOT EXISTS public.tides_pipeline_status (
  night TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  finished_at TIMESTAMPTZ NULL,
  ingested_count INTEGER DEFAULT 0,
  classified_count INTEGER DEFAULT 0,
  last_message TEXT NULL
);

CREATE TABLE IF NOT EXISTS public.tides_pipeline_event (
  id BIGSERIAL PRIMARY KEY,
  night TEXT NOT NULL,
  module TEXT NOT NULL,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tps_night ON public.tides_pipeline_event (night, created_at DESC);
```

Classification persistence
- By default, results are written via tides_pipe/modules/classifiers/classification_store.py.
- Example generic table:
```sql
CREATE TABLE IF NOT EXISTS public.tides_classification (
  tides_id BIGINT NOT NULL,
  method TEXT NOT NULL,
  label TEXT NULL,
  score DOUBLE PRECISION NULL,
  raw_json JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tides_id, method)
);
```
Adjust SQL to your schema if you’re writing into existing TiDES/TOM tables.

---

## Running locally without Docker

Prereqs: Python 3.11, Postgres client libs.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DB_HOST=...
export DB_NAME=...
# ... other envs (see above)

# Run watcher (will call the API)
uvicorn tides_pipe.ingest_api:app --host 0.0.0.0 --port 8001
# In another terminal:
python tides_pipe/watcher.py
```

Run manager one-shot for a given night:
```bash
python tides_pipe/manager.py --night 20250129 --one-shot
```

---

## Classifiers

- SNID and NGSF run as separate FastAPI services (see modules/classifiers/*_docker).
- Manager calls them via HTTP using tides_pipe/modules/classifiers/client.py.
- Ensure spectra mount is shared so APIs can access uploaded or referenced files:
  - SNID: uploads the spectrum file by default (no shared path needed)
  - NGSF: default endpoint accepts JSON “file” name; set its working_dir to mounted /data/spectra so basenames resolve

Compose mounts (example):
- pipeline: ./spectra -> /data/spectra
- snid_api: ./spectra -> /data/spectra:ro
- ngsf_api: ./spectra -> /data/spectra:ro and working_dir: /data/spectra

Override endpoints with CLASSIFIER_SNID_ENDPOINT and CLASSIFIER_NGSF_ENDPOINT if needed.

---

## Watcher

- Monitors deliveries_dir for new/modified .fits files.
- Debounces per night, then POSTs to pipeline API /ingest with night.
- Config via env: DELIVERIES_DIR and PIPELINE_API.

---

## TOM integration

- Easiest: TOM reads pipeline status and classifications from the same Postgres.
- Optional: expose a tiny read-only status API (tides_pipe/status_api.py) that TOM can call.

---

## Logging

- Logs to stdout (visible via docker logs) and to files under LOG_DIR (default /data/logs or config.log_dir).
- Set LOG_LEVEL=DEBUG for verbose output.

---

## Troubleshooting

- API not reachable:
  - docker compose ps
  - curl http://localhost:8001/health
- Watcher not triggering:
  - Verify mounts; watcher and pipeline must share the same host deliveries_dir
  - Ensure night folder format deliveries/<YYYYMMDD>/...
- DB errors:
  - Check DB_* envs are present in the pipeline container (docker compose exec pipeline env | grep DB_)
  - Verify SSL mode matches server policy
- Classifier timeouts:
  - Increase CLASSIFIER_HTTP_TIMEOUT
  - Check classifier service logs (docker logs snid_api / ngsf_api)

---

## Contributing

- Fork, branch, and submit PRs.
- Keep modules self-contained; prefer config-driven steps over hard-coding.
- Avoid committing secrets; use .env and CI secrets.

---

## License

Specify