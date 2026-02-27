"""
NGSF classification handler for tides_pipe.

Mirrors the structure of snid_handler.py but drives the NGSF classifier API.

Key differences from the SNID handler
--------------------------------------
* The NGSF API (``tides-shared-services/ngsf_docker/ngsf_api.py``) accepts a
  Pydantic ``Params`` model with:

    - ``spectrum`` (str): **full path** to the spectrum file — the API reads it
      directly (FITS or ASCII) and converts internally.
    - ``output_dir`` (str, **required**): per-spectrum output directory; must
      start with ``/ngsf_api_runs`` (enforced by the API).
    - All the usual NGSF tuning knobs (z, z_min/max, resolution, …).

* The API is internally async (asyncio task + deduplication lock) but returns
  a complete HTTP response once NGSF finishes.  Response shape::

      {"success": True, "data": {"file_path": "<output_dir>/spectrum.csv",
                                  "table": [<top-10 CSV rows as dicts>]}}

  The CSV rows are **embedded in the response**, so we parse them directly
  without polling the filesystem.  ``_wait_for_csv`` is kept as a fallback
  for edge cases where the table is absent.

* The result CSV is always named ``spectrum.csv`` at ``output_dir/spectrum.csv``.
* Results are written to ``pipeline_classification_superfit`` (NGSF = Next
  Generation SuperFit).  Writing to ``pipeline_classification_global`` is the
  sole responsibility of the M25 combiner.
"""

import csv
import json
import logging
import math
import os
import stat
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

try:
    from scipy import stats as scipy_stats
    _SCIPY = True
except Exception:
    _SCIPY = False
    scipy_stats = None  # type: ignore

from tides_pipe.modules.classifiers.ngsf_defaults import ngsf_params_from_config

try:
    from tides_pipe.utils import db as dbutil
except Exception:
    dbutil = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NGSF_API_URL = (
    os.getenv("CLASSIFIER_NGSF_URL")
    or os.getenv("NGSF_API_URL")
    or "http://ngsf_api:8000"
).rstrip("/")
NGSF_API_ENDPOINT = os.getenv("CLASSIFIER_NGSF_ENDPOINT", "/ngsf_params/").lstrip("/")
# Root directory (pipeline-container perspective) where output dirs are created.
# Must start with /ngsf_api_runs — the real API enforces this.
NGSF_API_OUT_ROOT = os.getenv("NGSF_API_OUT_ROOT", "/ngsf_api_runs")
NGSF_API_TIMEOUT = int(os.getenv("NGSF_API_TIMEOUT", "600"))

log = logging.getLogger("tides_ngsf")

# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _mkdir_777(path: str, logger) -> None:
    old_umask = os.umask(0)
    try:
        os.makedirs(path, mode=0o777, exist_ok=True)
    finally:
        os.umask(old_umask)
    try:
        os.chmod(path, 0o777)
    except Exception as exc:
        logger.warning(f"[ngsf] chmod 777 failed for {path}: {exc}")


# ---------------------------------------------------------------------------
# Broad-class mapping (from NGSF_output.py / Milligan+25)
# ---------------------------------------------------------------------------

_CLASS_DICT: Dict[str, list] = {
    "Ia":    ["Ia 91T-like", "Ia-norm", "Ia 91bg-like", "Ia 99aa-like"],
    "Ibc":   ["Ibn", "Ib", "Ic", "Ic-BL", "Ic-pec", "IIb", "IIb-flash"],
    "II":    ["II", "II-flash", "IIn"],
    "SL":    ["SLSN-II", "SLSN-IIn", "SLSN-I", "SLSN-Ib", "SLSN-IIb"],
    "Non":   ["TDE H", "TDE He", "TDE H+He", "FBOT", "ILRT"],
    "other": [
        "Ia 02es-like", "Ia-02cx like", "Ia-CSM-(ambigious)", "Ia-pec",
        "Ia-CSM", "Ia-rapid", "Ca-Ia", "super_chandra", "SN - Imposter",
        "computed", "Ca-Ib",
    ],
}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _as_float(x) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except Exception:
        return None


def _pretty_json(obj: dict) -> str:
    try:
        return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(obj)


def _infer_from_path(spectrum_path: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        night = os.path.basename(os.path.dirname(spectrum_path))
        base = os.path.basename(spectrum_path)
        stem, _ = os.path.splitext(base)
        if stem.endswith("_spectrum"):
            stem = stem[:-9]
        return (night if night.isdigit() else None), stem
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Core CSV parsing (port of NGSF_output.py concatenate())
# ---------------------------------------------------------------------------

def _parse_ngsf_rows(rows: List[dict], result_file: Optional[str] = None) -> Dict[str, Any]:
    """Apply the chi²-based broad-class aggregation from ``NGSF_output.py`` to
    a list of row dicts (either read from the CSV on disk or taken directly
    from the ``api_resp["data"]["table"]`` list returned by the real API).

    Each row dict is expected to have at minimum the keys:
        ``SPECTRUM``, ``Z``, ``SN``, ``CHI2/dof``

    Returns a M25-compatible dict::

        {
            "result_file":   str | None,
            "sn_type":       str,    # Ia / Ibc / II / SL / Non / other
            "probability":   float,
            "z":             float | None,
            "zerr":          None,
            "phase":         None,
            "Ia_prob":       float,
            ...
            "best_template": str,
        }
    """
    out: Dict[str, Any] = {"result_file": result_file}

    # Filter out -inf CHI2 rows (mirrors NGSF_output.py mask)
    valid_rows: List[dict] = []
    for row in rows:
        try:
            chi = float(row.get("CHI2/dof", "0") or "0")
            if not math.isinf(chi):
                valid_rows.append(row)
        except Exception:
            valid_rows.append(row)

    if not valid_rows:
        log.warning("[ngsf] No valid rows after filtering")
        return out

    # Best match: first row (NGSF outputs sorted by descending chi²)
    best_row = valid_rows[0]
    pred_z = _as_float(best_row.get("Z"))

    # Truncate raw template name at first '/'
    best_sn_full = str(best_row.get("SN", ""))
    slash = best_sn_full.find("/")

    # Chi²-based broad-class probabilities over the top-9 matches
    prob_dict: Dict[str, float] = {k: 0.0 for k in _CLASS_DICT}
    n_matches = min(len(valid_rows), 9)

    for row in valid_rows[:n_matches]:
        sn_full = str(row.get("SN", ""))
        sl = sn_full.find("/")
        sn_broad = sn_full[:sl] if sl != -1 else sn_full

        try:
            chi_val = float(row.get("CHI2/dof", "0") or "0")
            if _SCIPY:
                sn_prob = float(scipy_stats.chi2.cdf(chi_val, 1))  # type: ignore[union-attr]
            else:
                sn_prob = max(0.0, chi_val)  # proxy weight without scipy
        except Exception:
            sn_prob = 0.0

        for broad_class, members in _CLASS_DICT.items():
            if sn_broad in members:
                prob_dict[broad_class] += sn_prob
                break

    total = sum(prob_dict.values()) or 1.0
    norm = {k: v / total for k, v in prob_dict.items()}

    best_class = max(norm, key=norm.get)  # type: ignore[arg-type]
    best_prob = norm[best_class]

    out.update(
        {
            "sn_type":       best_class,
            "probability":   best_prob,
            "z":             pred_z,
            "zerr":          None,
            "phase":         None,
            "Ia_prob":       norm.get("Ia", 0.0),
            "Ibc_prob":      norm.get("Ibc", 0.0),
            "II_prob":       norm.get("II", 0.0),
            "SL_prob":       norm.get("SL", 0.0),
            "Non_prob":      norm.get("Non", 0.0),
            "other_prob":    norm.get("other", 0.0),
            "best_template": best_sn_full,
        }
    )
    return out


def _parse_ngsf_csv(csv_path: str) -> Dict[str, Any]:
    """Read *csv_path* from disk and delegate to ``_parse_ngsf_rows``."""
    out: Dict[str, Any] = {"result_file": csv_path}
    try:
        rows: List[dict] = []
        with open(csv_path, newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append(row)
    except Exception as exc:
        log.warning(f"[ngsf] Could not read CSV {csv_path}: {exc}")
        return out
    parsed = _parse_ngsf_rows(rows, result_file=csv_path)
    out.update(parsed)
    return out


# ---------------------------------------------------------------------------
# Handler class
# ---------------------------------------------------------------------------

class NgsfHandler:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.log = log
        self._db_conn = None

    def _target_dir(self, night: str, tides_specid: Union[str, int]) -> str:
        """Create and return the per-spectrum output dir under NGSF_API_OUT_ROOT.

        The real API enforces that ``output_dir`` starts with ``/ngsf_api_runs``
        so we always build it under that root.
        """
        night_dir = os.path.join(NGSF_API_OUT_ROOT, str(night))
        obj_dir   = os.path.join(night_dir, str(tides_specid))
        for path in (NGSF_API_OUT_ROOT, night_dir, obj_dir):
            try:
                _mkdir_777(path, self.log)
            except PermissionError as exc:
                self.log.warning(f"[ngsf] PermissionError creating {path}: {exc}")
                raise
        return obj_dir

    # ------------------------------------------------------------------
    # API interaction
    # ------------------------------------------------------------------

    def _post_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST *payload* as JSON to the NGSF API and return the response dict.

        The NGSF API is synchronous: it blocks until NGSF has finished and the
        result CSV has been moved, then returns ``{"path": "<stem>.csv"}``.
        """
        url = f"{NGSF_API_URL}/{NGSF_API_ENDPOINT}"
        self.log.info(f"[ngsf] POST {url}\n{_pretty_json(payload)}")
        try:
            with httpx.Client(timeout=NGSF_API_TIMEOUT) as cx:
                r = cx.post(url, json=payload)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                return {"status": "ok"}
        except httpx.HTTPStatusError as exc:
            body = exc.response.text if exc.response is not None else ""
            self.log.error(
                f"[ngsf] API error {exc.response.status_code if exc.response else ''} at {url}\n"
                f"Response body:\n{body}"
            )
            raise

    def _wait_for_csv(self, csv_path: str, timeout_s: int = 30) -> Optional[str]:
        """Poll until *csv_path* (always ``output_dir/spectrum.csv``) exists
        and is stable (size unchanged for 2 s).

        Used as a robustness guard when the shared filesystem lags behind
        the API response — the primary parse path uses the rows already
        embedded in the API JSON response.
        """

        start = time.time()
        last_sz = -1
        stable = 0
        while time.time() - start < timeout_s:
            if os.path.exists(csv_path):
                try:
                    sz = os.path.getsize(csv_path)
                    if sz == last_sz:
                        stable += 1
                    else:
                        stable = 0
                        last_sz = sz
                    if stable >= 2:
                        return csv_path
                except FileNotFoundError:
                    pass
            time.sleep(1)
        return None

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _db_connect(self):
        if self._db_conn is not None:
            return self._db_conn
        if not dbutil:
            self.log.warning("[ngsf] dbutil not available; results will not be saved to DB")
            return None
        try:
            creds = dbutil.load_creds(self.config)
            self._db_conn = dbutil.connect(creds)
            return self._db_conn
        except Exception as exc:
            self.log.warning(f"[ngsf] DB connect failed; results will not be saved to DB: {exc}")
            return None

    def _resolve_tides_specid(
        self,
        spectrum_path: str,
        tides_id: Union[str, int],
        night: str,
    ) -> Optional[int]:
        conn = self._db_connect()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT tides_specid FROM tides_spec WHERE filepath = %s LIMIT 1",
                        (spectrum_path,),
                    )
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        return int(row[0])
            except Exception as exc:
                self.log.debug(f"[ngsf] tides_specid lookup failed: {exc}")
        # Deterministic fallback
        try:
            import zlib
            base = f"{tides_id}|{night}|{os.path.basename(spectrum_path)}"
            return int(zlib.crc32(base.encode("utf-8")) & 0x7FFFFFFF)
        except Exception:
            return None

    def _save_result_unified(
        self,
        tides_id: str,
        tides_specid: Optional[int],
        night: str,
        result: Dict[str, Any],
    ) -> None:
        """Upsert classification into ``pipeline_classification_superfit``.

        Writing to ``pipeline_classification_global`` is the sole
        responsibility of the M25 combiner — do NOT write it here.
        """
        conn = self._db_connect()
        if not conn:
            return

        sn_type      = result.get("sn_type")
        prob         = _as_float(result.get("probability"))
        z            = _as_float(result.get("z"))
        results_file = result.get("result_file")
        version      = ((self.config.get("ngsf") or {}).get("defaults") or {}).get("version") or ""

        # Per-spectrum upsert (preferred when tides_specid is available)
        if tides_specid is not None:
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE pipeline_classification_superfit
                               SET tides_id = %s, sn_type = %s, probability = %s,
                                   version = %s, z = %s, results_file = %s
                             WHERE tides_specid = %s
                            """,
                            (int(tides_id), sn_type, prob, version, z,
                             results_file, int(tides_specid)),
                        )
                        if cur.rowcount == 0:
                            cur.execute(
                                """
                                INSERT INTO pipeline_classification_superfit
                                    (tides_specid, tides_id, sn_type, probability,
                                     version, z, results_file)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                """,
                                (int(tides_specid), int(tides_id), sn_type, prob,
                                 version, z, results_file),
                            )
                self.log.info(
                    f"[ngsf] Upserted per-spectrum classification "
                    f"(tides_specid={tides_specid}, tides_id={tides_id})"
                )
                return
            except Exception as exc:
                self.log.debug(
                    f"[ngsf] Per-spectrum upsert failed; falling back to tides_id: {exc}"
                )

        # Legacy fallback — tides_id only
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE pipeline_classification_superfit
                           SET sn_type = %s, probability = %s, version = %s, z = %s
                         WHERE tides_id = %s
                        """,
                        (sn_type, prob, version, z, int(tides_id)),
                    )
                    if cur.rowcount == 0:
                        cur.execute(
                            """
                            INSERT INTO pipeline_classification_superfit
                                (tides_id, sn_type, probability, version, z)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (int(tides_id), sn_type, prob, version, z),
                        )
            self.log.info(f"[ngsf] Upserted legacy classification (tides_id={tides_id})")
        except Exception as exc:
            self.log.warning(f"[ngsf] Unified DB write failed: {exc}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def classify(
        self,
        spectrum_path: str,
        night: Optional[str] = None,
        tides_id: Optional[Union[str, int]] = None,
        tides_specid: Optional[int] = None,
        ngsf_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run NGSF on *spectrum_path* and return a M25-compatible result dict.

        Return dict keys
        ----------------
        status        : ``"ok"`` | ``"error"``
        sn_type       : broad class (Ia / Ibc / II / SL / Non / other)
        probability   : float 0–1
        z             : float | None
        zerr          : None  (NGSF does not report zerr)
        phase         : None  (NGSF epoch params are priors, not fitted output)
        result_file   : path to the raw NGSF CSV
        Ia_prob …     : per-class probability floats (for logging/traceability)
        best_template : raw NGSF template name of the top match
        """
        # Resolve night / tides_id if not provided
        if not night or not tides_id:
            inf_night, inf_id = _infer_from_path(spectrum_path)
            night = night or inf_night
            tides_id = tides_id or inf_id
        if not night or not tides_id:
            raise ValueError("Unable to infer night/tides_id; provide them explicitly")

        if tides_specid is None:
            tides_specid = self._resolve_tides_specid(spectrum_path, tides_id, night)

        # Create per-spectrum output directory (real API requires output_dir
        # to start with /ngsf_api_runs and will reject anything else)
        out_dir = self._target_dir(str(night), str(tides_specid))
        # The CSV result is always named spectrum.csv inside out_dir
        csv_path = os.path.join(out_dir, "spectrum.csv")

        # Build request parameters
        if ngsf_params is None:
            self.log.info("[ngsf] No params provided by caller; using defaults from config")
            params = ngsf_params_from_config(self.config)
        else:
            params = ngsf_params

        # Real API Params model: key is 'spectrum' (full path), plus 'output_dir'
        payload: Dict[str, Any] = {
            "spectrum":   spectrum_path,
            "output_dir": out_dir,
        }
        payload.update({k: v for k, v in params.items() if v is not None})

        self.log.info(
            "[ngsf] Preflight: "
            f"spectrum_exists={os.path.exists(spectrum_path)} "
            f"out_dir={out_dir}"
        )

        # POST to API — async internally but returns a complete response
        api_resp = self._post_job(payload)
        self.log.info(f"[ngsf] API response keys: {list(api_resp.keys()) if isinstance(api_resp, dict) else api_resp}")

        result: Dict[str, Any]

        # Primary: parse the rows already embedded in the API response
        # Response shape: {"success": True, "data": {"file_path": "...", "table": [...]}}
        resp_rows = (api_resp.get("data") or {}).get("table") if isinstance(api_resp, dict) else None
        resp_file = (api_resp.get("data") or {}).get("file_path") if isinstance(api_resp, dict) else None

        if resp_rows:
            result = {"status": "ok", "result_file": resp_file or csv_path}
            result.update(_parse_ngsf_rows(resp_rows, result_file=resp_file or csv_path))
            self.log.info(
                f"[ngsf] Parsed from response rows: sn_type={result.get('sn_type')} "
                f"prob={result.get('probability', 0.0):.3f} z={result.get('z')}"
            )
        else:
            # Fallback: wait for the CSV to appear on the shared filesystem
            self.log.info(f"[ngsf] No table in response; polling for CSV at {csv_path}")
            resolved = self._wait_for_csv(csv_path, timeout_s=60)
            if resolved:
                result = {"status": "ok"}
                result.update(_parse_ngsf_csv(resolved))
                self.log.info(
                    f"[ngsf] Parsed from disk CSV: sn_type={result.get('sn_type')} "
                    f"prob={result.get('probability', 0.0):.3f} z={result.get('z')}"
                )
            else:
                msg = f"[ngsf] No result: API returned no table and CSV not found at {csv_path}"
                self.log.error(msg)
                result = {"status": "error", "message": msg}

        # Persist to pipeline_classification_superfit
        try:
            self._save_result_unified(str(tides_id), tides_specid, str(night), result)
        except Exception as exc:
            self.log.warning(f"[ngsf] unified save failed: {exc}")

        # Write a done sentinel alongside the CSV for monitoring
        done_path = os.path.join(out_dir, "ngsf_done.txt")
        try:
            with open(done_path, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "status":      result.get("status"),
                            "sn_type":     result.get("sn_type"),
                            "probability": result.get("probability"),
                            "z":           result.get("z"),
                            "result_file": result.get("result_file"),
                        }
                    )
                    + "\n"
                )
        except Exception as exc:
            self.log.warning(f"[ngsf] failed to write ngsf_done.txt: {exc}")

        return result


# Dynamic-loader compatibility (mirrors snid_handler.py)
Handler = NgsfHandler