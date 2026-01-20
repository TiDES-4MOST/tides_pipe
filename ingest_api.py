from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import logging
from tides_pipe.manager import PipelineManager

app = FastAPI(title="tides_pipeline_api")
log = logging.getLogger("tides_pipeline_api")
logging.basicConfig(level=logging.INFO)

class IngestRequest(BaseModel):
    night: str  # e.g. "20250129"
    env: str = "operations"

def _run_manager_once(night: str, env: str):
    try:
        # Only process operations for now, as requested
        if env != "operations":
            log.info(f"Skipping pipeline run for env='{env}' (currently only 'operations' supported)")
            return
            
        mgr = PipelineManager()
        mgr.run(night=night, one_shot=True, sleep_seconds=1, env=env)
    except Exception as e:
        log.exception(f"Manager failed for night {night}: {e}")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
def ingest(req: IngestRequest, bg: BackgroundTasks):
    if not req.night or not req.night.isdigit():
        raise HTTPException(status_code=400, detail="night must be YYYYMMDD")
    bg.add_task(_run_manager_once, req.night, req.env)
    return {"accepted": True, "night": req.night, "env": req.env}

class ClassifyRequest(BaseModel):
    night: str                  # e.g. "20250129"
    objects: list[int] | None = None  # optional subset of IDs

def _run_classify_once(night: str, objects: list[int] | None):
    try:
        mgr = PipelineManager()
        mgr.classify_night(night, objects=objects)
    except Exception as e:
        log.exception(f"Classification failed for night {night}: {e}")

@app.post("/classify")
def classify(req: ClassifyRequest, bg: BackgroundTasks):
    if not req.night or not req.night.isdigit():
        raise HTTPException(status_code=400, detail="night must be YYYYMMDD")
    if req.objects is not None and not all(isinstance(x, int) for x in req.objects):
        raise HTTPException(status_code=400, detail="objects must be list of ints")
    bg.add_task(_run_classify_once, req.night, req.objects)
    return {"accepted": True, "night": req.night, "count": (len(req.objects) if req.objects else None)}