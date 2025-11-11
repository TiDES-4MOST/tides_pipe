# filepath: /Users/pwise/4MOST/tides/tides_pipe/classifiers/snid.py
import os
import time
import glob
import json
import logging
from typing import Optional, Dict, Any

import httpx
try:
    import h5py
except Exception:
    h5py = None

from tides_pipe.modules.classifiers.classification_store import save_result
from classification_handlers import ClassificationHandler  # base class

SNID_API_URL = (
    os.getenv("CLASSIFIER_SNID_URL")
    or os.getenv("SNID_API_URL")
    or "http://snid_api:8000"
).rstrip("/")
SNID_API_ENDPOINT = os.getenv("SNID_API_ENDPOINT", "/snid_params/").lstrip("/")
SNID_API_OUT_ROOT = os.getenv("SNID_API_OUT_ROOT", "/snid_api_runs/pipeline_out")
SNID_API_TIMEOUT = int(os.getenv("SNID_API_TIMEOUT", "600"))

class SnidHandler(ClassificationHandler):
    def __init__(self, config_file: Optional[str] = None):
        super().__init__(config_file)
        self.log = logging.getLogger("tides_snid")

    def _target_dir(self, night: str, tides_id: str | int) -> str:
        d = os.path.join(SNID_API_OUT_ROOT, str(night), str(tides_id))
        os.makedirs(d, exist_ok=True)
        return d

    def _post_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{SNID_API_URL}/{SNID_API_ENDPOINT}"
        with httpx.Client(timeout=120) as cx:
            r = cx.post(url, json=payload)
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("application/json"):
                return r.json()
            return {"status": "ok"}

    def _wait_for_hdf5(self, out_dir: str, timeout_s: int) -> Optional[str]:
        start = time.time()
        last_sz = -1
        stable = 0
        while time.time() - start < timeout_s:
            files = sorted(glob.glob(os.path.join(out_dir, "*.h5")) + glob.glob(os.path.join(out_dir, "*.hdf5")))
            if files:
                f = files[0]
                try:
                    sz = os.path.getsize(f)
                    if sz == last_sz:
                        stable += 1
                    else:
                        stable = 0
                        last_sz = sz
                    if stable >= 3:
                        return f
                except FileNotFoundError:
                    pass
            time.sleep(1)
        return None

    def _parse_hdf5(self, h5_path: str) -> Dict[str, Any]:
        if not h5py:
            return {"result_file": h5_path}
        out: Dict[str, Any] = {"result_file": h5_path}
        try:
            with h5py.File(h5_path, "r") as f:
                for key in ("verdict", "best_template", "redshift", "rlap", "sn", "phase"):
                    if key in f:
                        try:
                            val = f[key][()]
                            if hasattr(val, "tolist"):
                                val = val.tolist()
                            if isinstance(val, bytes):
                                val = val.decode("utf-8", "ignore")
                            out[key] = val
                        except Exception:
                            pass
        except Exception as e:
            self.log.warning(f"Failed to parse HDF5 {h5_path}: {e}")
        return out

    def classify(self, spectrum_path: str, night: str, tides_id: str | int, snid_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Run SNID classification via remote API.
        spectrum_path is directly readable by the SNID API (shared mount).
        """
        out_dir = self._target_dir(night, tides_id)
        self.log.info(f"[snid] out_dir={out_dir}")
        # Match Django client: flat payload with 'spectrum' and 'output_dir' plus params
        payload = {"spectrum": spectrum_path, "output_dir": out_dir}
        if snid_params:
            payload.update(snid_params)

        api_resp = self._post_job(payload)
        self.log.info(f"[snid] API response: {api_resp}")

        h5_path = self._wait_for_hdf5(out_dir, SNID_API_TIMEOUT)
        if not h5_path:
            msg = f"[snid] No HDF5 result produced in {out_dir}"
            self.log.error(msg)
            result = {"status": "error", "message": msg, "out_dir": out_dir}
        else:
            result = {"status": "ok", "out_dir": out_dir, "result_file": h5_path}
            result.update(self._parse_hdf5(h5_path))
            self.log.info(f"[snid] Result file: {h5_path}")

        # Persist classification
        try:
            save_result(
                code="snid",
                tides_id=str(tides_id),
                night=str(night),
                result=result,
                result_path=h5_path if result.get("status") == "ok" else None,
            )
        except TypeError:
            try:
                save_result("snid", str(tides_id), result)
            except Exception as e:
                self.log.warning(f"[snid] save_result failed: {e}")
        except Exception as e:
            self.log.warning(f"[snid] save_result failed: {e}")

        # done.txt marker
        try:
            with open(os.path.join(out_dir, "done.txt"), "w") as f:
                f.write(json.dumps({
                    "status": result.get("status"),
                    "result_file": result.get("result_file"),
                    "api_resp": api_resp
                }) + "\n")
        except Exception as e:
            self.log.warning(f"[snid] failed to write done.txt: {e}")

        return result