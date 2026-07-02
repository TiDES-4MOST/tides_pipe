"""
tom_combiner.py — Fine-grained TOM classification combiner.

Alternative to m25_combiner.  Produces TidesClass + TidesClassSubClass level
output by mapping raw classifier strings to canonical names loaded from
config/tom_combiner_config.yml (or an explicit path passed via config).

Select in config.yml::

    classification:
        combiner: tom   # 'tom' (fine-grained) or 'm25' (default coarse)
    tom_combiner:
        config_file: /path/to/tom_combiner_config.yml  # optional override

Interface mirrors m25_combiner so manager.py can call either transparently:

    from tides_pipe.modules.classifiers.tom_combiner import combine, store_global

Each classifier result dict passed to combine() is expected to have at minimum:
    {
        "sn_type":     str | None,   # broad or fine class string
        "probability": float | None,
        "z":           float | None,
        "zerr":        float | None,
        "phase":       float | None,
    }
SNID results may additionally carry:
    {
        "best_template": str,  # e.g. "sn1991bg-Ia-91bg" — used for subclass hint
    }
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

log = logging.getLogger("tom_combiner")

# Default config path — resolved relative to this file so it works whether
# the repo is checked out or installed as a package.
_DEFAULT_CONFIG = os.path.normpath(os.path.join(
    os.path.dirname(__file__),   # .../modules/classifiers/
    "..", "..", "config",         # .../config/
    "tom_combiner_config.yml",
))


def _load_mapping_config(config_path: Optional[str] = None) -> dict:
    """Load and return the YAML mapping config.  Falls back to empty dicts."""
    path = config_path or _DEFAULT_CONFIG
    try:
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}
        log.debug(f"[tom_combiner] Loaded mapping config from {path}")
        return data
    except FileNotFoundError:
        log.warning(
            f"[tom_combiner] Config not found at {path}; "
            "tidesclass FKs will be NULL until the file is present."
        )
        return {}
    except Exception as e:
        log.error(f"[tom_combiner] Failed to load config from {path}: {e}")
        return {}


def _build_raw_to_tidesclass(cfg: dict) -> Dict[str, str]:
    return {str(k): str(v) for k, v in (cfg.get("raw_to_tidesclass") or {}).items()}


def _build_raw_to_subclass(cfg: dict) -> Dict[Tuple[str, str], Optional[str]]:
    """Parse "ClassName|raw_string": value entries from the YAML."""
    out: Dict[Tuple[str, str], Optional[str]] = {}
    for key, val in (cfg.get("raw_to_subclass") or {}).items():
        parts = str(key).split("|", 1)
        if len(parts) == 2:
            out[(parts[0].strip(), parts[1].strip())] = (
                str(val) if val not in (None, "null") else None
            )
    return out


def _build_template_hints(cfg: dict) -> List[Tuple[str, str, str]]:
    out = []
    for entry in (cfg.get("template_subclass_hints") or []):
        p = entry.get("pattern")
        c = entry.get("tidesclass")
        s = entry.get("subclass")
        if p and c and s:
            out.append((str(p), str(c), str(s)))
    return out


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


def _resolve_tidesclass(sn_type: Optional[str], mapping: Dict[str, str]) -> Optional[str]:
    """Return canonical TidesClass name for *sn_type*, or None."""
    if not sn_type:
        return None
    return mapping.get(sn_type)


def _resolve_subclass(
    tidesclass_name: Optional[str],
    sn_type: Optional[str],
    best_template: Optional[str],
    subclass_map: Dict[Tuple[str, str], str],
    template_hints: List[Tuple[str, str, str]],
) -> Optional[str]:
    """Return tides_class_subclass.sub_class string, or None."""
    if not tidesclass_name:
        return None

    # 1. Direct (tidesclass_name, sn_type) lookup
    if sn_type:
        val = subclass_map.get((tidesclass_name, sn_type))
        if val is not None:   # explicit None means "no subclass"
            return val

    # 2. Template-name pattern hints
    if best_template:
        tmpl_lower = best_template.lower()
        for pattern, cls, sub in template_hints:
            if pattern.lower() in tmpl_lower and cls == tidesclass_name:
                return sub

    return None


def _best_prob(result: Optional[Dict[str, Any]]) -> float:
    if not result:
        return 0.0
    return _float(
        result.get("probability") or result.get("Best_prob") or result.get("score") or result.get("rlap")
    ) or 0.0


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
    siren: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
    mapping: Optional[Dict[str, str]] = None,
    subclass_map: Optional[Dict[Tuple[str, str], Optional[str]]] = None,
    template_hints: Optional[List[Tuple[str, str, str]]] = None,
) -> Dict[str, Any]:
    """
    Apply the TOM fine-grained combination logic.

    Parameters
    ----------
    snid, ngsf, dash, siren : classifier result dicts (any may be None)
    config_path    : path to tom_combiner_config.yml (overrides default)
    mapping        : override for raw_to_tidesclass (skips YAML load if given)
    subclass_map   : override for raw_to_subclass (skips YAML load if given)
    template_hints : override for template_subclass_hints (skips YAML load if given)

    Returns
    -------
    dict with keys:
        tidesclass_name        : str | None
        tidesclass_subclass_name: str | None
        sn_type                : str
        probability            : float | None
        z, zerr, phase         : float | None
        notes                  : str
        classifiers_used       : list[str]
    """
    # Load mappings from YAML unless explicit overrides provided
    if mapping is None or subclass_map is None or template_hints is None:
        cfg = _load_mapping_config(config_path)
        if mapping is None:
            mapping = _build_raw_to_tidesclass(cfg)
        if subclass_map is None:
            subclass_map = _build_raw_to_subclass(cfg)
        if template_hints is None:
            template_hints = _build_template_hints(cfg)

    used: List[str] = []
    if snid  is not None: used.append("snid")
    if ngsf  is not None: used.append("ngsf")
    if dash  is not None: used.append("dash")
    if siren is not None: used.append("siren")

    # ------------------------------------------------------------------
    # Main class: reuse M25 logic but applied to canonical names.
    # Map each classifier's raw sn_type -> canonical name, then vote.
    # Priority: NGSF (chi²-based) ≥ DASH ≥ SNID ≥ SIREN (placeholder).
    # ------------------------------------------------------------------
    dash_raw   = _str((dash  or {}).get("sn_type"))
    ngsf_raw   = _str((ngsf  or {}).get("sn_type"))
    snid_raw   = _str((snid  or {}).get("sn_type"))
    siren_raw  = _str((siren or {}).get("sn_type"))

    dash_cls   = _resolve_tidesclass(dash_raw,  mapping)
    ngsf_cls   = _resolve_tidesclass(ngsf_raw,  mapping)
    snid_cls   = _resolve_tidesclass(snid_raw,  mapping)
    siren_cls  = _resolve_tidesclass(siren_raw, mapping)

    # Prefer NGSF z (most reliable), then SNID, then DASH, then SIREN
    z     = _best_z(ngsf)    or _best_z(snid)    or _best_z(dash)    or _best_z(siren)
    zerr  = _best_zerr(ngsf) or _best_zerr(snid) or _best_zerr(dash) or _best_zerr(siren)
    phase = _best_phase(snid) or _best_phase(dash) or _best_phase(ngsf) or _best_phase(siren)

    # Voting: NGSF+DASH agree → use NGSF (broad classes reliable together);
    # else NGSF alone; else SNID; else SIREN; else "Other".
    notes_parts: List[str] = []
    winning_tidesclass: Optional[str] = None
    winning_raw: Optional[str] = None
    winning_prob: Optional[float] = None
    winning_source: str = "none"

    if dash_cls and ngsf_cls and dash_cls == ngsf_cls:
        winning_tidesclass = ngsf_cls
        winning_raw  = ngsf_raw
        winning_prob = _best_prob(ngsf)
        winning_source = "ngsf+dash"
        notes_parts.append(f"NGSF+DASH agree={winning_tidesclass}")
    elif ngsf_cls:
        winning_tidesclass = ngsf_cls
        winning_raw  = ngsf_raw
        winning_prob = _best_prob(ngsf)
        winning_source = "ngsf"
        if dash_cls:
            notes_parts.append(f"NGSF={ngsf_cls} DASH={dash_cls} disagree; using NGSF")
        else:
            notes_parts.append(f"NGSF={ngsf_cls}")
    elif snid_cls:
        winning_tidesclass = snid_cls
        winning_raw  = snid_raw
        winning_prob = _best_prob(snid)
        winning_source = "snid"
        notes_parts.append(f"SNID-only={snid_cls}")
    elif siren_cls:
        winning_tidesclass = siren_cls
        winning_raw  = siren_raw
        winning_prob = _best_prob(siren)
        winning_source = "siren"
        notes_parts.append(f"SIREN-only={siren_cls}")
    else:
        winning_tidesclass = "Other"
        winning_raw  = None
        winning_prob = None
        notes_parts.append("no classifier produced a usable result")

    # ------------------------------------------------------------------
    # Subclass: prefer SNID (most specific subtype info), then template hints.
    # For SNID, use winning_raw only when SNID was the winner *or* SNID's
    # mapped class agrees with the winning class.
    # ------------------------------------------------------------------
    snid_template = _str((snid or {}).get("best_template")) if snid else None

    subclass_raw: Optional[str] = None
    if snid and snid_cls == winning_tidesclass:
        # SNID agrees with winning class — trust its subtype
        subclass_raw = snid_raw
    elif winning_source == "snid":
        subclass_raw = snid_raw

    tidesclass_subclass_name = _resolve_subclass(
        winning_tidesclass, subclass_raw, snid_template, subclass_map, template_hints
    )

    if tidesclass_subclass_name:
        notes_parts.append(f"subclass={tidesclass_subclass_name}")

    notes_parts.append(f"winning={winning_source}")
    notes = "; ".join(notes_parts)

    log.debug(
        f"[tom_combiner] {winning_tidesclass} / {tidesclass_subclass_name} "
        f"(prob={winning_prob}) | {notes}"
    )

    return {
        "tidesclass_name":         winning_tidesclass,
        "tidesclass_subclass_name": tidesclass_subclass_name,
        "sn_type":                 winning_raw or winning_tidesclass or "Other",
        "probability":             winning_prob,
        "z":                       z,
        "zerr":                    zerr,
        "phase":                   phase,
        "notes":                   notes,
        "classifiers_used":        used,
    }


def store_global(
    conn,
    tides_id: int,
    tides_specid: Optional[int],
    result: Dict[str, Any],
    version: str = "",
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    Upsert the fine-grained combined result into pipeline_classification_global,
    resolving tidesclass_id and tidesclass_subclass_id from the DB.

    Conflict key: tides_specid (UNIQUE).  Falls back to tides_id if None.
    """
    lg = logger or log

    sn_type              = result.get("sn_type")
    prob                 = result.get("probability")
    z                    = result.get("z")
    zerr                 = result.get("zerr")
    phase                = result.get("phase")
    notes                = result.get("notes", "")
    tidesclass_name      = result.get("tidesclass_name")
    subclass_name        = result.get("tidesclass_subclass_name")

    # Resolve tidesclass_id
    tidesclass_id: Optional[int] = None
    if tidesclass_name:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM tides_class WHERE name = %s LIMIT 1",
                    (tidesclass_name,),
                )
                row = cur.fetchone()
                if row:
                    tidesclass_id = row[0]
                else:
                    lg.debug(f"[tom_combiner] No tides_class row for name={tidesclass_name!r}")
        except Exception as e:
            lg.debug(f"[tom_combiner] tidesclass lookup failed: {e}")

    # Resolve tidesclass_subclass_id
    tidesclass_subclass_id: Optional[int] = None
    if subclass_name and tidesclass_id is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM tides_class_subclass
                        WHERE main_class_id = %s AND sub_class = %s LIMIT 1""",
                    (tidesclass_id, subclass_name),
                )
                row = cur.fetchone()
                if row:
                    tidesclass_subclass_id = row[0]
                else:
                    lg.debug(
                        f"[tom_combiner] No subclass row for "
                        f"main_class_id={tidesclass_id} sub_class={subclass_name!r}"
                    )
        except Exception as e:
            lg.debug(f"[tom_combiner] subclass lookup failed: {e}")

    try:
        with conn:
            with conn.cursor() as cur:
                if tides_specid is not None:
                    cur.execute(
                        """
                        INSERT INTO pipeline_classification_global
                            (tides_specid, tides_id, sn_type, tidesclass_id,
                             tidesclass_subclass_id, probability, version,
                             z, zerr, phase, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tides_specid) DO UPDATE SET
                            tides_id               = EXCLUDED.tides_id,
                            sn_type                = EXCLUDED.sn_type,
                            tidesclass_id          = EXCLUDED.tidesclass_id,
                            tidesclass_subclass_id = EXCLUDED.tidesclass_subclass_id,
                            probability            = EXCLUDED.probability,
                            version                = EXCLUDED.version,
                            z                      = EXCLUDED.z,
                            zerr                   = EXCLUDED.zerr,
                            phase                  = EXCLUDED.phase,
                            notes                  = EXCLUDED.notes
                        """,
                        (
                            int(tides_specid), int(tides_id), sn_type,
                            tidesclass_id, tidesclass_subclass_id,
                            prob, version, z, zerr, phase, notes,
                        ),
                    )
                    lg.info(
                        f"[tom_combiner] Upserted _global "
                        f"tides_id={tides_id} tides_specid={tides_specid} "
                        f"class={tidesclass_name}({tidesclass_id}) "
                        f"subclass={subclass_name}({tidesclass_subclass_id}) prob={prob}"
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO pipeline_classification_global
                            (tides_id, sn_type, tidesclass_id,
                             tidesclass_subclass_id, probability, version,
                             z, zerr, phase, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tides_id) DO UPDATE SET
                            sn_type                = EXCLUDED.sn_type,
                            tidesclass_id          = EXCLUDED.tidesclass_id,
                            tidesclass_subclass_id = EXCLUDED.tidesclass_subclass_id,
                            probability            = EXCLUDED.probability,
                            version                = EXCLUDED.version,
                            z                      = EXCLUDED.z,
                            zerr                   = EXCLUDED.zerr,
                            phase                  = EXCLUDED.phase,
                            notes                  = EXCLUDED.notes
                        """,
                        (
                            int(tides_id), sn_type,
                            tidesclass_id, tidesclass_subclass_id,
                            prob, version, z, zerr, phase, notes,
                        ),
                    )
                    lg.info(
                        f"[tom_combiner] Upserted _global (tides_id-only) "
                        f"tides_id={tides_id} class={tidesclass_name} "
                        f"subclass={subclass_name}"
                    )
    except Exception as e:
        lg.error(
            f"[tom_combiner] Failed to upsert _global for tides_id={tides_id}: {e}"
        )
