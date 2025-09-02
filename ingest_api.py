from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, yaml, traceback
from tides_pipe.modules.data_ingestion import DataIngestion

app = FastAPI(title="tides_pipe")

class IngestRequest(BaseModel):
    night: str  # e.g. "20250129"

def _load_cfg():
    cfg_path = os.getenv("TIDES_CONFIG", "/app/tides_pipe/config/config.yml")
    with open(cfg_path, "r") as f:
        cfg = yaml.safe_load(f) or {}
    # Allow overriding paths by env
    cfg.setdefault("paths", {})
    cfg["paths"]["deliveries_dir"] = os.getenv("DELIVERIES_DIR", cfg["paths"].get("deliveries_dir", "/data/deliveries"))
    cfg["paths"]["spectra_dir"] = os.getenv("SPECTRA_DIR", cfg["paths"].get("spectra_dir", "/data/spectra"))
    cfg["paths"]["static_plots_dir"] = os.getenv("STATIC_PLOTS_DIR", cfg["paths"].get("static_plots_dir", "/data/static/plots"))
    return cfg

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest(req: IngestRequest):
    try:
        cfg = _load_cfg()
        di = DataIngestion(cfg)
        objs = di.process_night(req.night)
        return {"ingested": len(objs), "objects": objs}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))