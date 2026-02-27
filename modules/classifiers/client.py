import os
import asyncio
from typing import Any, Dict, Optional
import httpx

SNID_URL = os.getenv("CLASSIFIER_SNID_URL", "http://snid_api:8000").rstrip("/")
SNID_ENDPOINT = os.getenv("CLASSIFIER_SNID_ENDPOINT", "/classify")
SNID_UPLOAD = os.getenv("CLASSIFIER_SNID_UPLOAD", "1") not in ("0", "false", "False")

NGSF_URL = (
    os.getenv("CLASSIFIER_NGSF_URL")
    or os.getenv("NGSF_API_URL")
    or "http://192.168.10.117:8001"
).rstrip("/")
NGSF_ENDPOINT = os.getenv("CLASSIFIER_NGSF_ENDPOINT", "/ngsf_params/")
NGSF_OUT_ROOT = os.getenv("NGSF_API_OUT_ROOT", "/ngsf_api_runs")

TIMEOUT = float(os.getenv("CLASSIFIER_HTTP_TIMEOUT", "180"))
RETRIES = int(os.getenv("CLASSIFIER_HTTP_RETRIES", "3"))

async def _post_with_retries(client: httpx.AsyncClient, url: str, *, files=None, data=None, json=None) -> httpx.Response:
    delay = 1.0
    last: Optional[Exception] = None
    for _ in range(RETRIES):
        try:
            r = await client.post(url, files=files, data=data, json=json, timeout=TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            await asyncio.sleep(delay)
            delay *= 1.5
    assert last
    raise last

async def classify_snid_async(tides_id: int, spectrum_path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Default: upload spectrum file to SNID FastAPI (set CLASSIFIER_SNID_UPLOAD=0 to send JSON instead).
    """
    url = SNID_URL + SNID_ENDPOINT
    async with httpx.AsyncClient() as client:
        if SNID_UPLOAD and os.path.exists(spectrum_path):
            with open(spectrum_path, "rb") as f:
                files = {"file": (os.path.basename(spectrum_path), f, "text/plain")}
                resp = await _post_with_retries(client, url, files=files, data=params or {})
                return resp.json()
        # JSON mode: API must accept a path visible to the SNID container
        payload = {"tides_id": tides_id, "file": spectrum_path, **(params or {})}
        resp = await _post_with_retries(client, url, json=payload)
        return resp.json()

async def classify_ngsf_async(
    tides_id: int,
    spectrum_path: str,
    output_dir: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Call the shared-services NGSF API (POST /ngsf_params/).

    The real Params model requires:
      - ``spectrum``   : full path to the spectrum file (readable by the API container)
      - ``output_dir`` : per-spectrum output dir under /ngsf_api_runs (API enforces this)
    All other NGSF tuning knobs are passed via *params*.

    Response: {"success": True, "data": {"file_path": "...", "table": [{...}, ...]}}
    """
    url = NGSF_URL + NGSF_ENDPOINT
    payload: Dict[str, Any] = {
        "spectrum":   spectrum_path,
        "output_dir": output_dir,
        **(params or {}),
    }
    async with httpx.AsyncClient() as client:
        resp = await _post_with_retries(client, url, json=payload)
        return resp.json()