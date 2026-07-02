"""
Milligan+25 (M25) TiDES classification combiner.

Wraps the logic from TiDES_pipeline/source/M25_pipeline.py as a clean,
importable function — no file I/O, no argparse, no qmostetc dependency.

Usage (per object, after all classifiers have run):

    from tides_pipe.modules.classifiers.m25_combiner import combine, store_global

    result = combine(snid=snid_result, ngsf=ngsf_result, dash=dash_result)
    store_global(conn, tides_id, tides_specid, result, logger=logger)

Each classifier result dict is expected to contain at minimum:
    {
        "sn_type":    str | None,   # broad class: "Ia", "II", "Ibc", "SL", "Non", etc.
        "probability": float | None,
        "z":          float | None,
        "zerr":       float | None,
        "phase":      float | None,
    }

`combine()` is robust to missing classifiers — pass None (or omit) for any
classifier that did not run or failed. M25 requires at minimum DASH or NGSF
to produce a non-'Other' result; if only SNID is available the best SNID
class is returned with a 'snid_only' note.
"""

import logging
from typing import Any, Dict, Optional

log = logging.getLogger("tides_m25")

# Maps M25 coarse output classes to canonical tides_class.name values.
# Used to populate tidesclass_id in pipeline_classification_global.
_M25_TO_TIDESCLASS: Dict[str, str] = {
    "Ia":    "SNIa",
    "Ibc":   "SNIbc",
    "II":    "SNII",
    "SL":    "SLSN-I",
    "Non":   "Other",
    "other": "Other",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _str(val) -> Optional[str]:
    return str(val).strip() if val is not None else None


def _float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except Exception:
        return None


def _best_class(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract the broad SN class from a classifier result dict."""
    if not result:
        return None
    return _str(result.get("sn_type") or result.get("Best_class") or result.get("label") or result.get("verdict"))


def _best_prob(result: Optional[Dict[str, Any]]) -> float:
    """Extract probability / confidence from a classifier result dict."""
    if not result:
        return 0.0
    return _float(result.get("probability") or result.get("Best_prob") or result.get("score") or result.get("rlap")) or 0.0


def _best_z(result: Optional[Dict[str, Any]]) -> Optional[float]:
    if not result:
        return None
    return _float(result.get("z") or result.get("redshift") or result.get("pred_z"))


def _best_zerr(result: Optional[Dict[str, Any]]) -> Optional[float]:
    if not result:
        return None
    return _float(result.get("zerr"))


def _best_phase(result: Optional[Dict[str, Any]]) -> Optional[float]:
    if not result:
        return None
    return _float(result.get("phase"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def combine(
    snid: Optional[Dict[str, Any]] = None,
    ngsf: Optional[Dict[str, Any]] = None,
    dash: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Apply the Milligan+25 combination logic to per-object classifier results.

    Returns a dict:
        {
            "sn_type":     str,           # combined broad class
            "probability": float | None,  # from winning classifier
            "z":           float | None,
            "zerr":        float | None,
            "phase":       float | None,
            "notes":       str,           # provenance / diagnostic string
            "classifiers_used": list[str],
        }
    """
    used = []
    if snid is not None:
        used.append("snid")
    if ngsf is not None:
        used.append("ngsf")
    if dash is not None:
        used.append("dash")

    dash_class = _best_class(dash)
    ngsf_class = _best_class(ngsf)
    snid_class = _best_class(snid)

    # Prefer NGSF z (more reliable), then SNID, then DASH
    z    = _best_z(ngsf)    or _best_z(snid)    or _best_z(dash)
    zerr = _best_zerr(ngsf) or _best_zerr(snid) or _best_zerr(dash)
    phase = _best_phase(snid) or _best_phase(dash) or _best_phase(ngsf)

    # ------------------------------------------------------------------ #
    # M25 logic (from TiDES_pipeline/source/M25_pipeline.py)             #
    # Original relied on rband_mag() for a bright-object check; here we   #
    # skip that (we don't have the spectrum at combine-time) and fall back #
    # to NGSF as the tie-breaker, consistent with the paper intent.       #
    # ------------------------------------------------------------------ #

    pipeline_class = "Other"
    notes_parts = []

    if dash_class and dash_class in ("Ia", "II"):
        if ngsf_class and ngsf_class == dash_class:
            # DASH and NGSF agree, use NGSF
            pipeline_class = ngsf_class
            notes_parts.append(f"DASH+NGSF agree={pipeline_class}")
        elif ngsf_class:
            # DASH says Ia/II but NGSF disagrees, trust NGSF (rband_mag bright path)
            pipeline_class = ngsf_class
            notes_parts.append(f"DASH={dash_class} NGSF={ngsf_class} disagree; using NGSF")
        else:
            # No NGSF, fall to DASH
            pipeline_class = dash_class
            notes_parts.append(f"NGSF missing; using DASH={dash_class}")
    elif ngsf_class:
        # DASH absent or not Ia/II, use NGSF
        pipeline_class = ngsf_class
        notes_parts.append(f"DASH not Ia/II; using NGSF={ngsf_class}")
    elif snid_class:
        # Neither DASH nor NGSF available, use SNID
        pipeline_class = snid_class
        notes_parts.append(f"SNID-only fallback={snid_class}")
    else:
        notes_parts.append("no classifier produced a result")

    # Winning classifier's probability
    if pipeline_class == ngsf_class and ngsf:
        prob = _best_prob(ngsf)
        winning = "ngsf"
    elif pipeline_class == dash_class and dash:
        prob = _best_prob(dash)
        winning = "dash"
    elif snid:
        prob = _best_prob(snid)
        winning = "snid"
    else:
        prob = None
        winning = "none"

    notes_parts.append(f"winning={winning}")
    notes = "; ".join(notes_parts)

    log.debug(f"[m25] combine result: {pipeline_class} (prob={prob}) | {notes}")

    return {
        "sn_type":         pipeline_class,
        "probability":     prob,
        "z":               z,
        "zerr":            zerr,
        "phase":           phase,
        "notes":           notes,
        "classifiers_used": used,
    }


def store_global(
    conn,
    tides_id: int,
    tides_specid: int,
    result: Dict[str, Any],
    version: str = "",
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    Upsert the M25 combined result into pipeline_classification_global.

    Conflict key is tides_specid (UNIQUE constraint on that column).
    Falls back to tides_id-only upsert if tides_specid is None.
    """
    lg = logger or log
    sn_type   = result.get("sn_type")
    prob      = result.get("probability")
    z         = result.get("z")
    zerr      = result.get("zerr")
    phase     = result.get("phase")
    notes     = result.get("notes", "")

    # Resolve tidesclass_id from tides_class table (M25 produces coarse classes).
    tidesclass_id: Optional[int] = None
    canonical_name = _M25_TO_TIDESCLASS.get(sn_type or "")
    if canonical_name:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM tides_class WHERE name = %s LIMIT 1",
                    (canonical_name,),
                )
                row = cur.fetchone()
                if row:
                    tidesclass_id = row[0]
        except Exception as e:
            lg.debug(f"[m25] tidesclass lookup failed for {canonical_name!r}: {e}")

    try:
        with conn:
            with conn.cursor() as cur:
                if tides_specid is not None:
                    cur.execute(
                        """
                        INSERT INTO pipeline_classification_global
                            (tides_specid, tides_id, sn_type, tidesclass_id,
                             probability, version, z, zerr, phase, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tides_specid) DO UPDATE SET
                            tides_id      = EXCLUDED.tides_id,
                            sn_type       = EXCLUDED.sn_type,
                            tidesclass_id = EXCLUDED.tidesclass_id,
                            probability   = EXCLUDED.probability,
                            version       = EXCLUDED.version,
                            z             = EXCLUDED.z,
                            zerr          = EXCLUDED.zerr,
                            phase         = EXCLUDED.phase,
                            notes         = EXCLUDED.notes
                        """,
                        (int(tides_specid), int(tides_id), sn_type, tidesclass_id,
                         prob, version, z, zerr, phase, notes),
                    )
                    lg.info(
                        f"[m25] Upserted pipeline_classification_global "
                        f"tides_id={tides_id} tides_specid={tides_specid} "
                        f"class={sn_type} tidesclass_id={tidesclass_id} prob={prob}"
                    )
                else:
                    # No tides_specid: upsert on tides_id (legacy fallback)
                    cur.execute(
                        """
                        INSERT INTO pipeline_classification_global
                            (tides_id, sn_type, tidesclass_id,
                             probability, version, z, zerr, phase, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tides_id) DO UPDATE SET
                            sn_type       = EXCLUDED.sn_type,
                            tidesclass_id = EXCLUDED.tidesclass_id,
                            probability   = EXCLUDED.probability,
                            version       = EXCLUDED.version,
                            z             = EXCLUDED.z,
                            zerr          = EXCLUDED.zerr,
                            phase         = EXCLUDED.phase,
                            notes         = EXCLUDED.notes
                        """,
                        (int(tides_id), sn_type, tidesclass_id,
                         prob, version, z, zerr, phase, notes),
                    )
                    lg.info(
                        f"[m25] Upserted pipeline_classification_global (tides_id-only) "
                        f"tides_id={tides_id} class={sn_type} tidesclass_id={tidesclass_id}"
                    )
    except Exception as e:
        lg.error(f"[m25] Failed to upsert pipeline_classification_global for tides_id={tides_id}: {e}")
