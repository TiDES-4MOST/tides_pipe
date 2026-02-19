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

# Avoid external store to keep a single unified DB write here
# from tides_pipe.modules.classifiers.classification_store import save_result
from tides_pipe.modules.classifiers.snid_defaults import snid_params_from_config
try:
    from tides_pipe.utils import db as dbutil  # provides load_creds/connect
except Exception:
    dbutil = None

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

def _mkdir_777(path: str, logger):
    # Make directory with mode 777, ignoring process umask
    old_umask = os.umask(0)
    try:
        os.makedirs(path, mode=0o777, exist_ok=True)
    finally:
        os.umask(old_umask)
    try:
        os.chmod(path, 0o777)  # in case it already existed
    except Exception as e:
        logger.warning(f"[snid] chmod 777 failed for {path}: {e}")
    try:
        st = os.stat(path)
        logger.info(f"[snid] dir perms {path} mode={oct(stat.S_IMODE(st.st_mode))} uid:gid={st.st_uid}:{st.st_gid}")
    except Exception:
        pass

class SnidHandler:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.log = log
        self._db_conn = None

    def _target_dir(self, night: str, tides_specid: Union[str, int]) -> str:
        root = SNID_API_OUT_ROOT
        night_dir = os.path.join(root, str(night))
        obj_dir = os.path.join(night_dir, str(tides_specid))
        target_dir = os.path.join(obj_dir, "target")  # some APIs copy inputs here

        for path in (root, night_dir, obj_dir, target_dir):
            try:
                _mkdir_777(path, self.log)
            except PermissionError as e:
                self._log_perm_issue(path, e)
                raise
        return obj_dir

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

    def _db_connect(self):
        if self._db_conn is not None:
            return self._db_conn
        if not dbutil:
            self.log.warning("[snid] dbutil not available; results will not be saved to DB")
            return None
        try:
            creds = dbutil.load_creds(self.config)
            self._db_conn = dbutil.connect(creds)
            return self._db_conn
        except Exception as e:
            self.log.warning(f"[snid] DB connect failed; results will not be saved to DB: {e}")
            return None

    def _resolve_tides_specid(self, spectrum_path: str, tides_id: Union[str, int], night: str) -> Optional[int]:
        """Try to resolve per-spectrum id from tides_spec using tides_specid PK column.
        """
        conn = self._db_connect()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT tides_specid
                        FROM tides_spec
                        WHERE filepath = %s
                        LIMIT 1
                    """, (spectrum_path,))
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        return int(row[0])
            except Exception as e:
                self.log.debug(f"[snid] tides_specid lookup failed: {e}")
        try:
            import zlib
            base = f"{tides_id}|{night}|{os.path.basename(spectrum_path)}"
            return int(zlib.crc32(base.encode('utf-8')) & 0x7FFFFFFF)
        except Exception:
            return None

    def _save_result_db(self, tides_id: str, night: str, result: Dict[str, Any], api_resp: Dict[str, Any] | None):
        conn = self._db_connect()
        if not conn:
            return
        table = ((self.config.get("snid") or {}).get("db") or {}).get("table", "snid_results")
        payload = {
            "status": result.get("status"),
            "verdict": result.get("verdict"),
            "best_template": result.get("best_template"),
            "redshift": result.get("redshift"),
            "rlap": result.get("rlap"),
            "sn": result.get("sn"),
            "phase": result.get("phase"),
            "result_file": result.get("result_file"),
            "out_dir": result.get("out_dir"),
            "api_resp": api_resp or {},
        }
        try:
            with conn:
                with conn.cursor() as cur:
                    # Ensure table exists (lightweight safety)
                    cur.execute(f"""
                        CREATE TABLE IF NOT EXISTS {table} (
                            tides_id      TEXT NOT NULL,
                            night         TEXT NOT NULL,
                            classifier    TEXT NOT NULL DEFAULT 'snid',
                            payload       JSONB NOT NULL,
                            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (tides_id, night, classifier)
                        );
                    """)
                    # Upsert current payload
                    cur.execute(
                        f"""
                        INSERT INTO {table} (tides_id, night, classifier, payload)
                        VALUES (%s, %s, 'snid', %s)
                        ON CONFLICT (tides_id, night, classifier)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW();
                        """,
                        (str(tides_id), str(night), json.dumps(payload)),
                    )
            self.log.info(f"[snid] Saved result to DB table {table} for tides_id={tides_id}, night={night}")
        except Exception as e:
            self.log.warning(f"[snid] Failed to save result to DB: {e}")

    def _save_result_unified(self, tides_id: str, tides_specid: Optional[int], night: str, result: Dict[str, Any], api_resp: Dict[str, Any] | None):
        """Unified per-spectrum classification write.
        Attempts per-spectrum upsert using tides_specid when available, while preserving tides_id for legacy.
        """
        conn = self._db_connect()
        if not conn:
            return
        # Extract classification fields
        sn_type = (
            result.get('verdict') or
            ((api_resp or {}).get('data') or {}).get('table', [{}])[0].get('typing')
        )
        # rlap
        rlap = result.get('rlap')
        if rlap is None:
            try:
                rlap = ((api_resp or {}).get('data') or {}).get('table', [{}])[0].get('rlap')
            except Exception:
                rlap = None
        if isinstance(rlap, (list, tuple)):
            rlap = rlap[0] if rlap else None
        try:
            rlap = float(rlap) if rlap is not None else None
        except Exception:
            rlap = None
        # z and phase
        def _as_float(x):
            try:
                return float(x) if x is not None else None
            except Exception:
                return None
        z = _as_float(result.get('redshift'))
        phase = _as_float(result.get('phase'))
        version = ((self.config.get("snid") or {}).get("defaults") or {}).get("version") or ""

        # Try per-spectrum upsert first (if schema supports tides_specid)
        try:
            with conn:
                with conn.cursor() as cur:
                    if tides_specid is not None:
                        # Get results_file path
                        results_file = result.get('result_file')
                        
                        cur.execute(
                            """
                            UPDATE pipeline_classification_snid
                            SET tides_id = %s, sn_type = %s, probability = %s, version = %s, z = %s, phase = %s, results_file = %s
                            WHERE tides_specid = %s
                            """,
                            (int(tides_id), sn_type, rlap, version, z, phase, results_file, int(tides_specid))
                        )
                        if cur.rowcount == 0:
                            cur.execute(
                                """
                                INSERT INTO pipeline_classification_snid (tides_specid, tides_id, sn_type, probability, version, z, phase, results_file)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (int(tides_specid), int(tides_id), sn_type, rlap, version, z, phase, results_file)
                            )
                        self.log.info(f"[snid] Upserted per-spectrum classification (tides_specid={tides_specid}, tides_id={tides_id})")
                        
                        # Also save to pipeline_classification_global (now has UNIQUE constraint on tides_specid)
                        try:
                            cur.execute(
                                """
                                INSERT INTO pipeline_classification_global (tides_specid, tides_id, sn_type, probability, version, z, phase, notes)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (tides_specid)
                                DO UPDATE SET 
                                    tides_id = EXCLUDED.tides_id,
                                    sn_type = EXCLUDED.sn_type, 
                                    probability = EXCLUDED.probability, 
                                    version = EXCLUDED.version, 
                                    z = EXCLUDED.z, 
                                    phase = EXCLUDED.phase, 
                                    notes = EXCLUDED.notes
                                """,
                                (int(tides_specid), int(tides_id), sn_type, rlap, version, z, phase, "snid classification")
                            )
                            self.log.info(f"[snid] Saved to pipeline_classification_global (tides_specid={tides_specid})")
                        except Exception as e:
                            self.log.warning(f"[snid] Failed to save to pipeline_classification_global: {e}")
                        
                        return
        except Exception as e:
            # Likely schema lacks tides_specid; fall back to tides_id-only write
            self.log.debug(f"[snid] Per-spectrum upsert failed; falling back: {e}")

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE pipeline_classification_snid
                        SET sn_type = %s, probability = %s, version = %s, z = %s, phase = %s
                        WHERE tides_id = %s
                        """,
                        (sn_type, rlap, version, z, phase, int(tides_id))
                    )
                    if cur.rowcount == 0:
                        cur.execute(
                            """
                            INSERT INTO pipeline_classification_snid (tides_id, sn_type, probability, version, z, phase)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (int(tides_id), sn_type, rlap, version, z, phase)
                        )
            self.log.info(f"[snid] Upserted legacy classification (tides_id={tides_id})")
        except Exception as e:
            self.log.warning(f"[snid] Unified DB write failed: {e}")

    def classify(
        self,
        spectrum_path: str,
        night: Optional[str] = None,
        tides_id: Optional[Union[str, int]] = None,
        tides_specid: Optional[int] = None,
        snid_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not night or not tides_id:
            inf_night, inf_id = _infer_from_path(spectrum_path)
            night = night or inf_night
            tides_id = tides_id or inf_id
        if not night or not tides_id:
            raise ValueError("Unable to infer night/tides_id; provide them explicitly")
        
        # Resolve tides_specid early if not provided
        if tides_specid is None:
            tides_specid = self._resolve_tides_specid(spectrum_path, tides_id, night)
        if not tides_specid:
            raise ValueError(f"Unable to resolve tides_specid for {spectrum_path}")

        out_dir = self._target_dir(str(night), str(tides_specid))
        self.log.info(f"[snid] out_dir={out_dir} (tides_specid={tides_specid})")

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

        # Unified per-spectrum DB write
        try:
            self._save_result_unified(str(tides_id), tides_specid, str(night), result, api_resp)
        except Exception as e:
            self.log.warning(f"[snid] unified save failed: {e}")

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