from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import logging
from tides_pipe.manager import PipelineManager

app = FastAPI(title="tides_pipeline_api")
log = logging.getLogger("tides_pipeline_api")
logging.basicConfig(level=logging.INFO)

class IngestRequest(BaseModel):
    night: str  # e.g. "20250129"

def _run_manager_once(night: str):
    try:
        mgr = PipelineManager()
        mgr.run(night=night, one_shot=True, sleep_seconds=1)
    except Exception as e:
        log.exception(f"Manager failed for night {night}: {e}")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest(req: IngestRequest, bg: BackgroundTasks):
    if not req.night or not req.night.isdigit():
        raise HTTPException(status_code=400, detail="night must be YYYYMMDD")
    bg.add_task(_run_manager_once, req.night)
    return {"accepted": True, "night": req.night}