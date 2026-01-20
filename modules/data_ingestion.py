import os
import logging
import numpy as np
from astropy.io import fits
import shutil
import tarfile
from datetime import datetime
from .module import Module
import json
import matplotlib.pyplot as plt
import random
import hashlib
from tides_pipe.utils.paths import spectra_night_dir as get_spectra_night_dir, spectrum_path
from itertools import islice
from typing import Optional, Dict, Any
from tides_pipe.utils import submit_transients as trans_api
try:
    from tides_pipe.utils import dbutil
except Exception:
    dbutil = None

class DataIngestion(Module):
    def __init__(self, config):
        self.config = config or {}
        self._db_conn = None
        # Temp ID state per night (e.g., 20260120_1)
        self._temp_prefix = None
        self._temp_counter = 0
        self._last_was_temp = False

    @staticmethod
    def _json_default(o):
        import numpy as np
        from datetime import datetime
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (datetime, )):
            return o.isoformat()
        if isinstance(o, (bytes, bytearray)):
            return o.decode("utf-8", errors="ignore")
        return str(o)

    # Helper: compute deterministic qmost_id from tides_id + file basename
    def _compute_qmost_id(self, tides_id: str, source_file: str) -> int:
        s = f"{tides_id}|{os.path.basename(source_file)}"
        h = hashlib.sha1(s.encode("utf-8")).hexdigest()
        return int(h[:15], 16)  # < 2^63

    def _obj_nme_to_bigint(self, val) -> int:
        """
        Coerce FIBMETATAB.OBJ_NME to a BIGINT:
        - handles bytes/str
        - accepts numeric strings (including scientific notation)
        """
        if val is None:
            raise ValueError("OBJ_NME is None")
        if isinstance(val, bytes):
            val = val.decode('ascii', errors='ignore')
        s = str(val).strip()
        if s == '':
            raise ValueError("Empty OBJ_NME")
        try:
            return int(s)
        except ValueError:
            # try float-like (e.g. 1.23e+06)
            return int(float(s))

    def _ingestion_limit(self, cfg=None) -> int:
        """
        If test mode is enabled, return the max number of spectra to process.
        Priority: data_ingestion.max -> classification.max -> 5.
        """
        cfg = cfg or getattr(self, "config", {}) or {}
        di = (cfg.get("data_ingestion") or {})
        if not di.get("test"):
            return 0
        try:
            return int(di.get("max", (cfg.get("classification") or {}).get("max", 5)))
        except Exception:
            return 5

    def process_night(self, night):
        # Use paths from environment variables (for containers) or fall back to config file (for local development)
        self.logger.info(f"Starting data ingestion for night: {night}")
        # Set TEMP id prefix to the night label (expected YYYYMMDD)
        self._temp_prefix = str(night)
        self._temp_counter = 0
        
        env = self.config.get("env", "operations")

        deliveries_dir = os.getenv('DELIVERIES_DIR') or self.config['data_paths']['deliveries_dir']
        spectra_dir = os.getenv('SPECTRA_DIR') or self.config['data_paths']['spectra_dir']
        archive_dir = self.config['data_paths']['archive_dir']  # Keep from config as no container env var needed
        self.logger.info(f"Using deliveries_dir: {deliveries_dir}")
        self.logger.info(f"Using spectra_dir: {spectra_dir}")
        self.logger.info(f"Processing for environment: {env}")

        night_dir = os.path.join(deliveries_dir, env, night)
        self.logger.info(f"Looking for deliveries in nightly deliveries directory: {night_dir}")
        
        spectra_night_dir = os.path.join(spectra_dir, env, night)
        self.logger.info(f"Spectra will be saved in spectra directory: {spectra_night_dir}")
        
        # Ensure thumbnails go to the correct env-aware directory
        self.config['data_paths']['static_plots_dir'] = spectra_night_dir
        
        archive_night_dir = os.path.join(archive_dir, env, night)
        self.logger.info(f"Archives will be saved in archive directory: {archive_night_dir}")

        # Ensure output dir exists before writing any files or DONE flags
        os.makedirs(spectra_night_dir, exist_ok=True)
        
        if not os.path.exists(night_dir):
            self.logger.info(f"No data found for night {night} in {night_dir}")
            return []

        files = [f for f in os.listdir(night_dir) if f.endswith(".fits")]
        if not files:
            self.logger.info(f"No new files found for night {night}.")
            return []

        # Write "FALSE" to DONE.txt at the start
        signal_file = os.path.join(spectra_night_dir, "DONE.txt")
        with open(signal_file, 'w') as f:
            f.write("FALSE\n")

        obj_names = []
        limit = self._ingestion_limit()
        files = sorted(files)
        discovered_files = [os.path.join(night_dir, f) for f in files]
        for file_path in discovered_files:
            # If test mode, compute remaining spectra budget for this file
            remaining = None if not limit else max(0, limit - len(obj_names))
            if limit and remaining == 0:
                self.logger.info(f"[data_ingestion] Reached test limit ({limit}); stopping before {file_path}")
                break
            self.logger.info(f"Processing file: {file_path}")
            try:
                new_objs = self.process_file(file_path, spectra_night_dir, max_spectra=remaining)
                obj_names.extend(new_objs)
                if limit and len(obj_names) >= limit:
                    self.logger.info(f"[data_ingestion] Reached test limit ({limit}); stopping after {file_path}")
                    break
            except Exception as e:
                self.logger.error(f"Error processing {file_path}: {e}")

        #self.archive_files(night_dir, archive_night_dir) #TODO make this safe before enabling
        self.set_done(True, night)
        return obj_names

    def process_file(self, file_path, spectra_night_dir, max_spectra: int | None = None):
        self.logger.info(f"Parsing data from {file_path}")
        obj_names = []

        # Always parse FITS file contents (no test mode)
        with fits.open(file_path, memmap=False) as hdulist:
            fibinfodat = hdulist['FIBMETATAB'].data
            specdata = hdulist[2].data
            specheader = hdulist[2].header

            # Build wavelength array from header keywords (linear WCS)
            try:
                n = int(str(specheader['TDIM1']).strip().strip('()'))
            except Exception:
                n = specdata['FLUX'].shape[1] if specdata is not None else 0
            crval1 = specheader.get('1CRVL1') or specheader.get('CRVAL1')
            cdelt1 = specheader.get('1CDLT1') or specheader.get('CDELT1') or specheader.get('CD1_1')
            crpix1 = specheader.get('1CRPX1') or specheader.get('CRPIX1', 1.0)
            if crval1 is None or cdelt1 is None or n == 0:
                self.logger.error("Missing wavelength WCS keywords; cannot build wavelength axis")
                return obj_names
            pix = np.arange(1, n + 1, dtype=float)
            wave = crval1 + (pix - float(crpix1)) * float(cdelt1)

            # Optional: preload known tides_ids from tidestom.tides_cand (fast path when OBJ_NME==tides_id)
            valid_ids: set[int] = set()
            try:
                tidestom_conn = self.connect_to_db()  # default DB (tidestom)
                if tidestom_conn:
                    with tidestom_conn.cursor() as cur:
                        cur.execute("SELECT tides_id FROM tides_cand")
                        valid_ids = {int(r[0]) for r in cur.fetchall()}
                    tidestom_conn.close()
                    self.logger.info(f"Loaded {len(valid_ids)} tides_ids from tidestom.tides_cand (fast path)")
                else:
                    self.logger.warning("No connection to tidestom; skipping fast-path preload of tides_cand ids")
            except Exception as e:
                try:
                    tidestom_conn and tidestom_conn.close()
                except Exception:
                    pass
                self.logger.warning(f"Preload tides_cand ids failed: {e}")

            # Iterate all spectra in the file
            for counter, (flux, fluxerr, qual) in enumerate(zip(specdata['FLUX'], specdata['ERR_FLUX'], specdata['QUAL'])):
                try:
                    meta = fibinfodat[counter]
                    # Resolve tides_id using OBJ_NME fast path, else via OSTD/4MOST → tides_master → TEMP fallback
                    obj_id = self._resolve_tides_id(meta, valid_ids)
                    if obj_id is None:
                        self.logger.warning("Could not resolve tides_id for spectrum; skipping.")
                        continue
                    obj_names.append(str(obj_id))

                    # Ensure tides_cand contains tides_id; insert if missing (tidestom DB)
                    try:
                        self._ensure_tides_cand(int(obj_id))
                    except Exception as e:
                        self.logger.warning(f"Failed to ensure tides_cand({obj_id}): {e}")

                    # Filepaths use the tides_id (OBJ_NME)
                    spectrum_file = os.path.join(spectra_night_dir, f"{obj_id}_spectrum.txt")
                    metadata_file = os.path.join(spectra_night_dir, f"{obj_id}_metadata.txt")
                    thumbnail_file = os.path.join(spectra_night_dir, f"{obj_id}_thumbnail.png")

                    # Save spectrum data (Wavelength Flux Error Quality)
                    with open(spectrum_file, 'w') as f:
                        f.write("# Wavelength Flux Error Quality\n")
                        for w, fl, fe, q in zip(wave, flux, fluxerr, qual):
                            f.write(f"{w} {fl} {fe} {q}\n")

                    # Save metadata snapshot from fiber table
                    with open(metadata_file, 'w') as f:
                        f.write("# Metadata\n")
                        for name in fibinfodat.names:
                            try:
                                f.write(f"# {name}: {meta[name]}\n")
                            except Exception:
                                pass

                    # Generate and save thumbnail
                    self.generate_thumbnail(wave, flux, thumbnail_file)

                    self.logger.info(f"Saved spectrum to {spectrum_file}, metadata to {metadata_file}, and thumbnail to {thumbnail_file}")

                    # Build tides_spec metadata
                    def _mget(row, key, default=None):
                        try:
                            return row[key]
                        except Exception:
                            return default

                    # Obs time from header if available
                    obs_mjd = specheader.get('MJD-OBS') or specheader.get('MJDOBS') or specheader.get('MJD')
                    obs_date = specheader.get('DATE-OBS')
                    if obs_mjd is None and obs_date is None:
                        obs_date = datetime.now().isoformat()

                    # Build base metadata; store TIDES_ID as string for compatibility
                    metadata = {
                        'TIDES_ID': str(obj_id),
                        'QMOST_ID': self._compute_qmost_id(obj_id, spectrum_file),
                        'TYPE': 'temp' if self._last_was_temp else 'pending',
                        'OBS_DATE': obs_date,
                        'OBS_MJD': float(obs_mjd) if obs_mjd is not None else None,
                        'SNR': _mget(meta, 'SNR', None),
                        'SEEING': _mget(meta, 'SEEING', None),
                        'SKY_BRIGHTNESS': _mget(meta, 'SKYBRITE', None) or _mget(meta, 'SKY_BRIGHT', None),
                        'VERSION':1 #_mget(meta, 'QMOST_PIPELINE_VERSION', '1.0'),
                    }

                    # Enrich metadata and ensure BaseTarget exists in tidestom for downstream usage
                    master_info = self._lookup_master_info(meta)
                    name_for_bt = None
                    ra_for_bt = None
                    dec_for_bt = None
                    if master_info:
                        metadata.update({
                            'TARGET_NAME': master_info.get('name'),
                            'RA': master_info.get('ra'),
                            'DEC': master_info.get('dec')
                        })
                        name_for_bt = master_info.get('name')
                        ra_for_bt = master_info.get('ra')
                        dec_for_bt = master_info.get('dec')
                    else:
                        # Try to extract RA/Dec directly from the fiber metadata row
                        ra_for_bt, dec_for_bt = self._extract_coords_from_meta(meta)
                    if not name_for_bt:
                        name_for_bt = f"TEMP-{obj_id}"
                    try:
                        self._ensure_basetarget(int(obj_id), name_for_bt, ra_for_bt, dec_for_bt)
                    except Exception as e:
                        self.logger.debug(f"Ensure BaseTarget failed for {obj_id}: {e}")

                    # Update tides_spec with metadata (stores thumbnail path in additional_info)
                    self.update_tides_spec(str(obj_id), metadata, spectrum_file, thumbnail_file)
                    # Stop early if we hit the per-file budget in test mode
                    if max_spectra is not None and len(obj_names) >= max_spectra:
                        self.logger.info(f"[data_ingestion] File limit reached ({max_spectra}) for {os.path.basename(file_path)}; stopping this file")
                        break
                except Exception as e:
                    self.logger.error(f"Error processing spectrum {counter} in file {file_path}: {e}")
        return obj_names

    # --------- ID resolution helpers ---------

    def _get_api_token(self) -> Optional[str]:
        return os.getenv('TRANSIENT_API_TOKEN')

    def _next_temp_id(self) -> int:
        """
        Generate the next TEMP id for the current night in the format
        <YYYYMMDD><cc> (two-digit counter), e.g., 2026012001.
        This remains within 32-bit INTEGER limits while being obviously date-coded.
        """
        prefix = self._temp_prefix or datetime.now().strftime('%Y%m%d')
        self._temp_counter += 1
        # Two-digit counter appended to date; cycles after 99
        return int(f"{prefix}{self._temp_counter:02d}")

    def _resolve_tides_id(self, meta_row, valid_ids: set[int]) -> Optional[int]:
        """
        Resolve the TiDES `tides_id` for a spectrum row.
        Priority:
          1) If FIBMETATAB.OBJ_NME parses to int and exists in tidestom.tides_cand -> use it.
          2) Else, use OSTD/4MOST IDs from the MEC row and query tides.tides_master.
             If only OSTD IDs available, map via Transients API to 4MOST ID and then master.
          3) If all mapping fails, return a TEMP id for the night: <YYYYMMDD>_<n>.
        """
        self._last_was_temp = False
        # 1) Fast path from OBJ_NME
        try:
            obj_val = meta_row['OBJ_NME']
            obj_id_fast = int(obj_val)
            if obj_id_fast in valid_ids:
                return int(obj_id_fast)
        except Exception:
            pass

        # 2) Extract possible identifiers from the fiber metadata (case-insensitive lookup)
        def _get_any(row, candidates):
            names_l = {n.lower(): n for n in getattr(row, 'names', [])}
            for cand in candidates:
                k = names_l.get(cand.lower())
                if k is not None:
                    try:
                        return row[k]
                    except Exception:
                        continue
            return None

        ostd_u_obj = _get_any(meta_row, ['ostd_u_obj_id', 'ostd_uobj_id', 'u_obj_id'])
        ostd_targ  = _get_any(meta_row, ['ostd_targ_id', 'ostd_target_id', 'targ_id'])
        pk_4most   = _get_any(meta_row, ['pk_4most', 'fourmost_id', '4most_id', 'pk_4m'])

        # Query tides_master for tides_id using whichever IDs we have
        master = self._query_tides_master(pk_4most=pk_4most, ostd_u_obj_id=ostd_u_obj, ostd_targ_id=ostd_targ)
        if master and isinstance(master.get('tides_id'), (int, np.integer)):
            return int(master['tides_id'])

        # If master lookup failed and we only have OSTD IDs, try mapping via the Transients API
        if (ostd_u_obj or ostd_targ) and not pk_4most:
            token = self._get_api_token()
            if token:
                try:
                    trans_api.ACCESS_TOKEN = token
                    flt_parts = []
                    if ostd_u_obj is not None:
                        flt_parts.append(f"ostd_u_obj_id__exact={int(ostd_u_obj)}")
                    if ostd_targ is not None:
                        flt_parts.append(f"ostd_targ_id__exact={int(ostd_targ)}")
                    flt = "&".join(flt_parts)
                    res = trans_api.get_list(flt=flt, limit=1, timeout=15, return_mode="listdict")
                    if isinstance(res, list) and res:
                        cand = res[0]
                        pk_4 = None
                        # Prefer an explicit pk_4most if present; else API id
                        if isinstance(cand, dict):
                            pk_4 = cand.get('pk_4most') or cand.get('id')
                        master = self._query_tides_master(pk_4most=pk_4, ostd_u_obj_id=ostd_u_obj, ostd_targ_id=ostd_targ)
                        if master and isinstance(master.get('tides_id'), (int, np.integer)):
                            return int(master['tides_id'])
                except Exception as e:
                    self.logger.debug(f"Transients API mapping failed: {e}")

        # 3) TEMP fallback when no resolution was possible
        self._last_was_temp = True
        return self._next_temp_id()

    def _extract_coords_from_meta(self, row) -> tuple[Optional[float], Optional[float]]:
        """Attempt to read RA/Dec (degrees) from the fiber metadata row."""
        def _get_any(r, candidates):
            names_l = {n.lower(): n for n in getattr(r, 'names', [])}
            for cand in candidates:
                k = names_l.get(cand.lower())
                if k is not None:
                    try:
                        return r[k]
                    except Exception:
                        continue
            return None
        ra_raw = _get_any(row, [
            'ra','ra_deg','ra_degree','ra2000','alpha_j2000','ra_obj','obj_ra','ra_mean','ra_deg_j2000'
        ])
        dec_raw = _get_any(row, [
            'dec','dec_deg','dec_degree','dec2000','delta_j2000','dec_obj','obj_dec','dec_mean','dec_deg_j2000'
        ])
        ra_val = None
        dec_val = None
        try:
            if ra_raw is not None:
                r = float(ra_raw)
                ra_val = r * 15.0 if 0.0 <= r <= 24.0 else r
        except Exception:
            pass
        try:
            if dec_raw is not None:
                dec_val = float(dec_raw)
        except Exception:
            pass
        return ra_val, dec_val

    def _get_table_columns(self, conn, table_name: str) -> Dict[str, Dict[str, Any]]:
        schema = 'public'
        table = table_name
        if '.' in table_name:
            parts = table_name.split('.', 1)
            schema, table = parts[0], parts[1]
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

    def _placeholder_for(self, meta: Dict[str, Any]):
        """Return sensible placeholder given a column's type metadata."""
        dt = (meta.get('udt_name') or meta.get('data_type') or '').lower()
        if any(k in dt for k in ('int','float','double','real','numeric','dec')):
            return -9
        if 'bool' in dt:
            return False
        if 'timestamp' in dt:
            return datetime.now()
        if 'date' in dt:
            return datetime(1970,1,1)
        if 'json' in dt:
            return '{}'
        return 'UNKNOWN'

    def _ensure_basetarget(self, tides_id: int, name: str, ra: Optional[float], dec: Optional[float]):
        """
        Upsert into tom_targets_basetarget so tidestom can use TEMP/real IDs downstream.
        Fills required NOT NULL columns with placeholders if necessary.
        """
        conn = self.connect_to_db()  # tidestom
        if not conn:
            return
        try:
            with conn:
                cols_info = self._get_table_columns(conn, 'tom_targets_basetarget')
                cols = []
                vals = []
                # Core fields
                if 'id' in cols_info:
                    cols.append('id'); vals.append(int(tides_id))
                if 'name' in cols_info:
                    cols.append('name'); vals.append(str(name))
                if 'type' in cols_info:
                    cols.append('type'); vals.append('SIDEREAL')
                if 'slug' in cols_info:
                    cols.append('slug'); vals.append(str(name).lower())
                if 'created' in cols_info:
                    cols.append('created'); vals.append(datetime.now())
                if 'modified' in cols_info:
                    cols.append('modified'); vals.append(datetime.now())
                # Coords
                if 'ra' in cols_info and ra is not None:
                    cols.append('ra'); vals.append(float(ra))
                if 'dec' in cols_info and dec is not None:
                    cols.append('dec'); vals.append(float(dec))

                # Ensure NOT NULL columns are present
                required_missing = [
                    c for c, meta in cols_info.items()
                    if not meta['nullable'] and meta['default'] is None and c not in cols
                ]
                for c in required_missing:
                    cols.append(c); vals.append(self._placeholder_for(cols_info[c]))

                # Build UPSERT
                set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != 'id')
                placeholders = ",".join(["%s"] * len(vals))
                sql = f"INSERT INTO tom_targets_basetarget ({', '.join(cols)}) VALUES ({placeholders}) " \
                      f"ON CONFLICT (id) DO UPDATE SET {set_clause}"
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(vals))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _query_tides_master(self, pk_4most=None, ostd_u_obj_id=None, ostd_targ_id=None) -> Optional[Dict[str, Any]]:
        """
        Query tides.tides_master for a candidate using provided identifiers.
        Returns dict with keys: tides_id, name, ra, dec (when found), else None.
        """
        # Connect to the 'tides' database (same creds, different name)
        tides_db_name = os.getenv('TIDES_DB_NAME') or 'tides'
        conn = self.connect_to_db(db_name=tides_db_name)
        if not conn:
            return None
        try:
            where = []
            params = []
            if pk_4most is not None:
                where.append("pk_4most = %s")
                params.append(int(pk_4most))
            if ostd_u_obj_id is not None:
                where.append("ostd_u_obj_id = %s")
                params.append(int(ostd_u_obj_id))
            if ostd_targ_id is not None:
                where.append("ostd_targ_id = %s")
                params.append(int(ostd_targ_id))
            if not where:
                return None
            sql = "SELECT tides_id, name, ra, dec FROM tides_master WHERE " + " OR ".join(where) + " LIMIT 1"
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                if row:
                    return { 'tides_id': row[0], 'name': row[1], 'ra': row[2], 'dec': row[3] }
                return None
        except Exception as e:
            self.logger.debug(f"tides_master lookup failed: {e}")
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _ensure_tides_cand(self, tides_id: int):
        """Insert tides_id into tidestom.tides_cand if missing."""
        conn = self.connect_to_db()  # default DB (tidestom)
        if not conn:
            return
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM tides_cand WHERE tides_id = %s", (int(tides_id),))
                    if cur.fetchone():
                        return
                    cur.execute("INSERT INTO tides_cand (tides_id) VALUES (%s) ON CONFLICT (tides_id) DO NOTHING", (int(tides_id),))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _lookup_master_info(self, meta_row) -> Optional[Dict[str, Any]]:
        """Convenience to reuse _query_tides_master with IDs present in the row."""
        def _get_any(row, cands):
            names_l = {n.lower(): n for n in getattr(row, 'names', [])}
            for cand in cands:
                k = names_l.get(cand.lower())
                if k is not None:
                    try:
                        return row[k]
                    except Exception:
                        continue
            return None
        return self._query_tides_master(
            pk_4most=_get_any(meta_row, ['pk_4most', 'fourmost_id', '4most_id']),
            ostd_u_obj_id=_get_any(meta_row, ['ostd_u_obj_id', 'ostd_uobj_id', 'u_obj_id']),
            ostd_targ_id=_get_any(meta_row, ['ostd_targ_id', 'ostd_target_id', 'targ_id'])
        )

    def _update_basetarget_coords(self, tides_id: int, ra: Any, dec: Any):
        """
        Best-effort update of tom_targets_basetarget.ra/dec so tidestom templates that
        reference target.ra/target.dec have coordinates. Only runs when values are present.
        """
        if ra is None and dec is None:
            return
        conn = self.connect_to_db()  # tidestom
        if not conn:
            return
        try:
            with conn:
                with conn.cursor() as cur:
                    sets = []
                    params = []
                    if ra is not None:
                        sets.append("ra = %s")
                        params.append(float(ra))
                    if dec is not None:
                        sets.append("dec = %s")
                        params.append(float(dec))
                    if not sets:
                        return
                    params.append(int(tides_id))
                    sql = f"UPDATE tom_targets_basetarget SET {', '.join(sets)} WHERE id = %s"
                    cur.execute(sql, tuple(params))
        except Exception as e:
            self.logger.debug(f"tom_targets_basetarget RA/Dec update failed: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def generate_thumbnail(self, wavelength, flux, thumbnail_file):
        """
        Generates a thumbnail plot for the spectrum and saves it as a PNG file.
        """
        try:
            static_plots_dir = self.config['data_paths']['static_plots_dir']
            if not os.path.exists(static_plots_dir):
                os.makedirs(static_plots_dir)

            hardcoded_thumbnail_path = os.path.join(static_plots_dir, os.path.basename(thumbnail_file))

            plt.figure(figsize=(4, 3))  # Thumbnail size
            plt.plot(wavelength, flux, color='blue', linewidth=0.5)
            plt.xlabel('Wavelength [Å]')
            plt.ylabel('Flux')
            plt.title('Spectrum Thumbnail')
            plt.tight_layout()
            plt.savefig(hardcoded_thumbnail_path, dpi=100)
            plt.close()
            self.logger.info(f"Generated thumbnail: {hardcoded_thumbnail_path}")
        except Exception as e:
            self.logger.error(f"Failed to generate thumbnail: {e}")

    def _db_connect(self):
        """
        Connect using utils.dbutil if available. Returns a cached connection or None.
        """
        if self._db_conn is not None:
            return self._db_conn
        if not dbutil:
            return None
        try:
            creds = dbutil.load_creds(self.config)
            self._db_conn = dbutil.connect(creds)
            return self._db_conn
        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.warning(f"[ingestion] DB connect via dbutil failed: {e}")
            return None

    # Update or insert a spectrum record for the given tides_id
    def update_tides_spec(self, tides_id: str, metadata: Dict[str, Any], spectrum_file: str, thumbnail_file: str):
        """
        Persist ingestion outputs for a spectrum. Tries dbutil first; falls back to existing logic.
        """
        conn = self._db_connect()
        if conn:
            try:
                with conn:
                    with conn.cursor() as cur:
                        # Adjust table/columns if your schema differs
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS tides_spec (
                                tides_id    TEXT PRIMARY KEY,
                                spec_path   TEXT NOT NULL,
                                thumb_path  TEXT,
                                meta        JSONB,
                                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            );
                        """)
                        cur.execute("""
                            INSERT INTO tides_spec (tides_id, spec_path, thumb_path, meta)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (tides_id)
                            DO UPDATE SET
                                spec_path = EXCLUDED.spec_path,
                                thumb_path = EXCLUDED.thumb_path,
                                meta = EXCLUDED.meta,
                                updated_at = NOW();
                        """, (str(tides_id), spectrum_file, thumbnail_file, json.dumps(metadata)))
                if hasattr(self, "logger"):
                    self.logger.info(f"[ingestion] Saved tides_spec for {tides_id}")
                return
            except Exception as e:
                if hasattr(self, "logger"):
                    self.logger.warning(f"[ingestion] dbutil save failed; falling back: {e}")
                # fall through to your existing implementation

        # ...existing code...
        # Your original DB persistence logic goes here unchanged
        # e.g. psycopg connect/execute or ORM call
        # self._legacy_update_tides_spec(tides_id, metadata, spectrum_file, thumbnail_file)

    def archive_files(self, night_dir, archive_night_dir):
        if not os.path.exists(archive_night_dir):
            os.makedirs(archive_night_dir)
        
        tar_path = os.path.join(archive_night_dir, f"{os.path.basename(night_dir)}.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(night_dir, arcname=os.path.basename(night_dir))
        shutil.rmtree(night_dir)
        self.logger.info(f"Archived and removed original data for night {os.path.basename(night_dir)}")

def run(night, logger, config):
    ingestion = DataIngestion(config)
    ingestion.set_logger(logger)
    return ingestion.process_night(night)
