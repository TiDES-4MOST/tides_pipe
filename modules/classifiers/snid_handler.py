# filepath: /Users/pwise/4MOST/tides/tides_pipe/classifiers/snid.py
import os
import time
import glob
import json
import logging
from typing import Optional, Dict, Any, Tuple, Union

import httpx
try:
    import h5py
except Exception:
    h5py = None

from tides_pipe.modules.classifiers.classification_store import save_result
from tides_pipe.modules.classifiers.snid_defaults import snid_params_from_config

SNID_API_URL = (
    os.getenv("CLASSIFIER_SNID_URL")
    or os.getenv("SNID_API_URL")
    or "http://snid_api:8000"
).rstrip("/")
SNID_API_ENDPOINT = os.getenv("SNID_API_ENDPOINT", "/snid_params/").lstrip("/")
SNID_API_OUT_ROOT = os.getenv("SNID_API_OUT_ROOT", "/snid_api_runs/pipeline_out")
SNID_API_TIMEOUT = int(os.getenv("SNID_API_TIMEOUT", "600"))

log = logging.getLogger("tides_snid")

def _infer_from_path(spectrum_path: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        night = os.path.basename(os.path.dirname(spectrum_path))
        base = os.path.basename(spectrum_path)
        stem, _ = os.path.splitext(base)
        if stem.endswith("_spectrum"):
            stem = stem[:-9]
        return night if night.isdigit() else None, stem
    except Exception:
        return None, None

def _pretty_json(obj: dict) -> str:
    try:
        return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(obj)

import stat
# Optional: group resolution if you want to set a specific group via env
try:
    import grp
except Exception:
    grp = None

def _chmod_2770(path: str, logger):
    try:
        os.chmod(path, 0o2770)  # rwx for user+group, setgid bit
    except Exception as e:
        logger.warning(f"[snid] chmod 2770 failed for {path}: {e}")

def _chgrp_if_requested(path: str, logger):
    """
    Optionally set group to SNID_API_GID or SNID_API_GROUP if provided.
    Safe no-op otherwise.
    """
    gid_env = os.getenv("SNID_API_GID")
    grp_env = os.getenv("SNID_API_GROUP")
    if not gid_env and not grp_env:
        return
    try:
        gid = int(gid_env) if gid_env else (grp.getgrnam(grp_env).gr_gid if grp else None)
        if gid is not None:
            os.chown(path, -1, gid)
    except Exception as e:
        logger.warning(f"[snid] chgrp failed for {path}: {e}")

class SnidHandler:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.log = log

    def _target_dir(self, night: str, tides_id: Union[str, int]) -> str:
        root = SNID_API_OUT_ROOT
        night_dir = os.path.join(root, str(night))
        d = os.path.join(night_dir, str(tides_id))

        # Create each level and chmod 775 (best-effort)
        for path in (root, night_dir, d):
            try:
                os.makedirs(path, mode=0o775, exist_ok=True)
            except PermissionError as e:
                self.log.error(f"[snid] mkdir failed: {path}: {e}")
                raise
            try:
                os.chmod(path, 0o775)
            except Exception as e:
                self.log.warning(f"[snid] chmod 775 failed for {path}: {e}")

        return d

    def _post_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{SNID_API_URL}/{SNID_API_ENDPOINT}"
        # Log the full payload we are sending
        self.log.info(f"[snid] POST {url} with payload:\n{_pretty_json(payload)}")
        try:
            with httpx.Client(timeout=120) as cx:
                r = cx.post(url, json=payload)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return {"status": "ok"}
        except httpx.HTTPStatusError as e:
            body = e.response.text if e.response is not None else ""
            self.log.error(
                f"[snid] API error {e.response.status_code if e.response else ''} at {url}\n"
                f"Response body:\n{body}"
            )
            raise

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

    def classify(
        self,
        spectrum_path: str,
        night: Optional[str] = None,
        tides_id: Optional[Union[str, int]] = None,
        snid_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not night or not tides_id:
            inf_night, inf_id = _infer_from_path(spectrum_path)
            night = night or inf_night
            tides_id = tides_id or inf_id
        if not night or not tides_id:
            raise ValueError("Unable to infer night/tides_id; provide them explicitly")

        out_dir = self._target_dir(str(night), str(tides_id))
        self.log.info(f"[snid] out_dir={out_dir}")

        # Prefer params provided by manager; fall back to config defaults only if missing
        if snid_params is None:
            self.log.info("[snid] No params provided by caller; using defaults from config")
            params = snid_params_from_config(self.config)
        else:
            params = snid_params

        payload = {"spectrum": spectrum_path, "output_dir": out_dir}
        payload.update({k: v for k, v in params.items() if v is not None})

        # Also log a preflight check for clarity
        self.log.info(
            "[snid] Preflight: "
            f"spectrum_exists={os.path.exists(spectrum_path)} "
            f"out_dir_exists={os.path.isdir(out_dir)} "
            f"out_dir_writable={os.access(out_dir, os.W_OK)}"
        )

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

        try:
            save_result(
                code="snid",
                tides_id=str(tides_id),
                night=str(night),
                result=result,
                result_path=(h5_path if result.get("status") == "ok" else None),
            )
        except TypeError:
            try:
                save_result("snid", str(tides_id), result)
            except Exception as e:
                self.log.warning(f"[snid] save_result failed: {e}")
        except Exception as e:
            self.log.warning(f"[snid] save_result failed: {e}")

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

# Keep dynamic-loader compatibility
Handler = SnidHandler