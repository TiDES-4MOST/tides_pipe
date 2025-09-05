from typing import Optional
from tides_pipe.modules import db as dbutil

def upsert_status(conn, night: str, state: str, *, ingested: Optional[int]=None,
                  classified: Optional[int]=None, message: Optional[str]=None,
                  finished: bool=False):
    sql = """
    INSERT INTO public.tides_pipeline_status (night, state, last_message, ingested_count, classified_count)
    VALUES (%s, %s, %s, COALESCE(%s,0), COALESCE(%s,0))
    ON CONFLICT (night) DO UPDATE
      SET state = EXCLUDED.state,
          last_message = COALESCE(EXCLUDED.last_message, public.tides_pipeline_status.last_message),
          ingested_count = COALESCE(EXCLUDED.ingested_count, public.tides_pipeline_status.ingested_count),
          classified_count = COALESCE(EXCLUDED.classified_count, public.tides_pipeline_status.classified_count),
          updated_at = NOW(),
          finished_at = CASE WHEN %s THEN NOW() ELSE public.tides_pipeline_status.finished_at END
    """
    with conn.cursor() as cur:
        cur.execute(sql, (night, state, message, ingested, classified, finished))
    conn.commit()

def add_event(conn, night: str, module: str, level: str, message: str):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.tides_pipeline_event (night, module, level, message) VALUES (%s,%s,%s,%s)",
            (night, module, level, message),
        )
    conn.commit()