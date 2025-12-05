import argparse
import os
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
from astropy.io import fits
import statistics
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _obj_nme_to_bigint(val) -> int:
    if val is None:
        raise ValueError("OBJ_NME is None")
    if isinstance(val, (bytes, bytearray)):
        val = val.decode('ascii', errors='ignore')
    s = str(val).strip()
    if s == '':
        raise ValueError("Empty OBJ_NME")
    try:
        return int(s)
    except ValueError:
        return int(float(s))


def collect_ids_from_mec(paths):
    ids = set()
    for p in paths:
        with fits.open(p, memmap=False) as hdul:
            fib = hdul['FIBMETATAB'].data
            for v in fib['OBJ_NME']:
                try:
                    ids.add(_obj_nme_to_bigint(v))
                except Exception:
                    pass
    return sorted(ids)


def collect_targets_from_mec(paths):
    """
    Return dict: {tides_id: {'ra': ra_deg or None, 'dec': dec_deg or None}}
    """
    def _first_col(h, names):
        names_l = {n.lower(): n for n in h.names}
        for cand in names:
            if cand in names_l:
                return names_l[cand]
        return None

    targets = {}
    for p in paths:
        with fits.open(p, memmap=False) as hdul:
            fib = hdul['FIBMETATAB'].data
            # Candidate column names (case-insensitive)
            # RA may be in hours; we'll convert if <= 24
            ra_col = _first_col(fib, [
                'ra', 'ra_deg', 'ra_degree', 'ra2000', 'alpha_j2000',
                'ra_obj', 'obj_ra', 'ra_mean', 'ra_deg_j2000'
            ])
            dec_col = _first_col(fib, [
                'dec', 'dec_deg', 'dec_degree', 'dec2000', 'delta_j2000',
                'dec_obj', 'obj_dec', 'dec_mean', 'dec_deg_j2000'
            ])
            obj_col = _first_col(fib, ['obj_nme', 'obj_name', 'target_name', 'object'])

            if obj_col is None:
                raise RuntimeError("FIBMETATAB missing OBJ_NME/OBJ_NAME column")

            for row in fib:
                try:
                    tid = _obj_nme_to_bigint(row[obj_col])
                except Exception:
                    continue

                ra_val = None
                dec_val = None
                try:
                    if ra_col is not None:
                        ra_raw = float(row[ra_col])
                        # Heuristic: RA in hours if within [0, 24]
                        if 0.0 <= ra_raw <= 24.0:
                            ra_val = ra_raw * 15.0
                        else:
                            ra_val = ra_raw
                except Exception:
                    pass
                try:
                    if dec_col is not None:
                        dec_val = float(row[dec_col])
                except Exception:
                    pass

                entry = targets.setdefault(tid, {'ra_list': [], 'dec_list': []})
                if ra_val is not None and not (ra_val != ra_val):  # not NaN
                    entry['ra_list'].append(ra_val)
                if dec_val is not None and not (dec_val != dec_val):
                    entry['dec_list'].append(dec_val)

    # Reduce to single ra/dec per id (median), allow None if no data
    reduced = {}
    for tid, d in targets.items():
        ra = statistics.median(d['ra_list']) if d['ra_list'] else None
        dec = statistics.median(d['dec_list']) if d['dec_list'] else None
        reduced[tid] = {'ra': ra, 'dec': dec}
    return reduced


def _split_schema_table(name: str):
    if '.' in name:
        schema, table = name.split('.', 1)
    else:
        schema, table = 'public', name
    return schema, table


def get_table_columns(conn, table_name):
    schema, table = _split_schema_table(table_name)
    sql = """
      SELECT column_name, is_nullable, column_default, data_type, udt_name
      FROM information_schema.columns
      WHERE table_schema = %s AND table_name = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema, table))
        return {
            r[0]: {
                'nullable': (r[1] == 'YES'),
                'default': r[2],
                'data_type': r[3],
                'udt_name': r[4],
            }
            for r in cur.fetchall()
        }


def upsert_basetarget(conn, targets, table_name, name_prefix, target_type, fix_seq=False):
    cols_info = get_table_columns(conn, table_name)
    now = datetime.now(timezone.utc)

    # Base columns we’ll provide
    cols = ['id', 'name']
    def vals_builder(tid): return [tid, f'{name_prefix}{tid}']

    if 'type' in cols_info:
        cols.append('type')
        prev = vals_builder
        def vals_builder(tid, p=prev): return p(tid) + [target_type]

    if 'slug' in cols_info:
        cols.append('slug')
        prev = vals_builder
        def vals_builder(tid, p=prev): return p(tid) + [f'{name_prefix}{tid}'.lower()]

    if 'created' in cols_info:
        cols.append('created')
        prev = vals_builder
        def vals_builder(tid, p=prev): return p(tid) + [now]

    if 'modified' in cols_info:
        cols.append('modified')
        prev = vals_builder
        def vals_builder(tid, p=prev): return p(tid) + [now]

    # Inject RA/Dec if columns exist (fallback to -9.0 to avoid None in templates)
    if 'ra' in cols_info:
        cols.append('ra')
        prev = vals_builder
        def vals_builder(tid, p=prev): return p(tid) + [targets[tid]['ra'] if targets[tid]['ra'] is not None else -9.0]
    if 'dec' in cols_info:
        cols.append('dec')
        prev = vals_builder
        def vals_builder(tid, p=prev): return p(tid) + [targets[tid]['dec'] if targets[tid]['dec'] is not None else -9.0]

    # Fill any remaining NOT NULL columns (no default) with placeholders
    def placeholder_for(col, meta):
        # Prefer sensible defaults for known fields
        if col == 'scheme':
            return 'ICRS'
        # Generic fallbacks by type
        dt = (meta.get('udt_name') or meta.get('data_type') or '').lower()
        if any(k in dt for k in ('int', 'float', 'double', 'real', 'numeric', 'dec')):
            return -9
        if 'bool' in dt:
            return False
        if 'timestamp' in dt:
            return now
        if 'date' in dt:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        if 'json' in dt:
            return '{}'
        # Text/other
        return 'UNKNOWN'

    required_missing = [
        c for c, meta in cols_info.items()
        if not meta['nullable'] and meta['default'] is None and c not in cols
    ]
    # Keep stable order for SQL
    cols.extend(required_missing)
    prev = vals_builder
    def vals_builder(tid, p=prev):
        base = p(tid)
        extra = [placeholder_for(c, cols_info[c]) for c in required_missing]
        return base + extra

    ids = sorted(targets.keys())
    data = [tuple(vals_builder(tid)) for tid in ids]
    tmpl = "(" + ",".join(["%s"] * len(cols)) + ")"
    set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != 'id')
    sql = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s ON CONFLICT (id) DO UPDATE SET {set_clause}"

    with conn.cursor() as cur:
        if data:
            execute_values(cur, sql, data, template=tmpl)

        if fix_seq and 'id' in cols_info:
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table_name,))
            seq = cur.fetchone()[0]
            if seq:
                cur.execute(
                    f"SELECT setval(%s, GREATEST((SELECT COALESCE(MAX(id), 1) FROM {table_name}), 1))",
                    (seq,)
                )


def upsert_tides_cand(conn, ids, table_name):
    # Minimal insert (assumes tides_id is UNIQUE/PK)
    data = [(tid,) for tid in ids]
    sql = f"INSERT INTO {table_name} (tides_id) VALUES %s ON CONFLICT (tides_id) DO NOTHING"
    with conn.cursor() as cur:
        if data:
            execute_values(cur, sql, data)


def main():
    ap = argparse.ArgumentParser(description="Seed tom_targets_basetarget and tides_cand from MEC FITS")
    ap.add_argument('--fits', nargs='+', help='Path(s) to MEC FITS file(s)')
    ap.add_argument('--host', default=os.getenv('DB_HOST', 'localhost'))
    ap.add_argument('--port', default=os.getenv('DB_PORT', '5432'))
    ap.add_argument('--dbname', default=os.getenv('DB_NAME', 'tides_db'))
    ap.add_argument('--user', default=os.getenv('DB_USER', 'tides'))
    ap.add_argument('--password', default=os.getenv('DB_PASSWORD', ''))
    ap.add_argument('--sslmode', default=os.getenv('DB_SSLMODE', 'prefer'))
    ap.add_argument('--target-table', default='tom_targets_basetarget')
    ap.add_argument('--cand-table', default='tides_cand')
    ap.add_argument('--name-prefix', default='TIDES-')
    ap.add_argument('--type', dest='target_type', default='SIDEREAL')
    ap.add_argument('--fix-seq', action='store_true', help='Fix base target id sequence to MAX(id)')
    args = ap.parse_args()

    targets = collect_targets_from_mec(args.fits)
    if not targets:
        print("No OBJ_NME ids found")
        return 1
    print(f"Found {len(targets)} unique OBJ_NME ids")

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
        sslmode=args.sslmode,
    )
    try:
        conn.autocommit = False
        upsert_basetarget(conn, targets, args.target_table, args.name_prefix, args.target_type, fix_seq=args.fix_seq)
        upsert_tides_cand(conn, sorted(targets.keys()), args.cand_table)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        return 2
    finally:
        conn.close()

    print("Seeding complete")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())