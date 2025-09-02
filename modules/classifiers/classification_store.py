import json
from typing import Any, Dict, Optional

# Map classifier results to (label, score)
def _extract_label_score(method: str, res: Dict[str, Any]) -> tuple[Optional[str], Optional[float]]:
    # Try common keys; adjust if your APIs differ
    label = res.get("label") or res.get("class") or res.get("type")
    score = res.get("score") or res.get("prob") or res.get("probability")
    try:
        score = None if score is None else float(score)
    except Exception:
        score = None
    return (label, score)

def save_result(conn, tides_id: int, method: str, res: Dict[str, Any], logger=None):
    """
    Upsert into a generic table: public.tides_classification
    Columns: tides_id BIGINT/INT, method TEXT, label TEXT NULL, score DOUBLE PREC NULL, raw_json JSONB, updated_at timestamptz default now()
    Create this table once in your DB, or adjust SQL below to your concrete schema (e.g., custom_code_pipelineclassificationglobal).
    """
    label, score = _extract_label_score(method, res)
    raw = json.dumps(res)
    sql = """
    INSERT INTO public.tides_classification (tides_id, method, label, score, raw_json)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (tides_id, method) DO UPDATE
      SET label = EXCLUDED.label,
          score = EXCLUDED.score,
          raw_json = EXCLUDED.raw_json,
          updated_at = NOW()
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (tides_id, method, label, score, raw))
        conn.commit()
        if logger:
            logger.info(f"Saved classification ({method}) for {tides_id}: {label} ({score})")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        if logger:
            logger.error(f"Failed to save classification ({method}) for {tides_id}: {e}")
        else:
            print(f"Failed to save classification ({method}) for {tides_id}: {e}")