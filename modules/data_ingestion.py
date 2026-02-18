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
from typing import Optional, Dict, Any, List
from tides_pipe.utils import submit_transients as trans_api
try:
    from tides_pipe.utils import dbutil
except Exception:
    dbutil = None
try:
    from .stacking import Stacking
except Exception as e:
    logging.getLogger(__name__).warning(f"Could not import Stacking module: {e}")
    Stacking = None

class DataIngestion(Module):
    def __init__(self, config):
        self.config = config or {}
        self._db_conn = None
        # Temp ID state per night (e.g., 20260120_1)
        self._temp_prefix = None
        self._temp_counter = 0
        self._last_was_temp = False
        # Control whether unresolved IDs fall back to TEMP IDs. Default: allowed (no dev special-case).
        def _as_bool(v, default=True):
            if v is None:
                return default
            s = str(v).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off"):
                return False
            return default
        env_allow_temp = os.getenv('INGEST_ALLOW_TEMP')
        cfg_allow_temp = ((self.config.get('data_ingestion') or {}).get('allow_temp'))
        # ENV overrides config; default True to keep environments consistent unless explicitly disabled
        self._allow_temp = _as_bool(env_allow_temp if env_allow_temp is not None else cfg_allow_temp, True)
        
        # Initialize stacking module if available and enabled
        stacking_config = self.config.get('data_ingestion', {}).get('stacking', {})
        self.stacking_enabled = stacking_config.get('enabled', True)  # Default: enabled
        self.stacking = Stacking(config) if Stacking is not None else None
        if self.stacking:
            self.logger = logging.getLogger(__name__)
            if self.stacking_enabled:
                self.logger.info("Stacking module initialized and ENABLED")
            else:
                self.logger.info("Stacking module initialized but DISABLED by config")

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
        env = self.config.get("env", "operations")
        obj_results = []  # List of {tides_id, tides_specid, filepath}

        # Always parse FITS file contents
        with fits.open(file_path, memmap=False) as hdulist:
            fibinfodat = hdulist['FIBMETATAB'].data
            specdata = hdulist[2].data
            specheader = hdulist[2].header
            primary_header = hdulist[0].header if len(hdulist) > 0 else {}
            try:
                obmetadat = hdulist['OBMETATAB'].data
            except Exception:
                obmetadat = None

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

            # Preload known tides_ids from tidestom.tides_cand
            valid_ids: set[int] = set()
            try:
                tidestom_conn = self.connect_to_db()
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

            def _row_get(row, candidates, default=None):
                if row is None:
                    return default
                try:
                    names = list(getattr(row, 'dtype', {}).names or [])
                except Exception:
                    names = []
                name_map = {str(n).lower(): n for n in names}
                for c in candidates:
                    key = name_map.get(str(c).lower())
                    if key is not None:
                        try:
                            return row[key]
                        except Exception:
                            continue
                return default

            # PHASE 1: Parse all spectra and collect by OBJ_UID
            self.logger.info("Phase 1: Parsing all spectra from FITS file...")
            spectra_by_obj_uid = {}  # obj_uid -> list of spectrum dicts
            
            for counter, (flux, ivar, qual) in enumerate(zip(specdata['FLUX'], specdata['FLUX_IVAR'], specdata['QUAL'])):
                try:
                    meta = fibinfodat[counter]
                    obrow = None
                    try:
                        if obmetadat is not None and counter < len(obmetadat):
                            obrow = obmetadat[counter]
                    except Exception:
                        obrow = None
                    
                    # Resolve tides_id from OBJ_UID
                    obj_id = self._resolve_tides_id(meta, valid_ids)
                    if obj_id is None:
                        self.logger.warning("Could not resolve tides_id for spectrum; skipping.")
                        continue
                    
                    # Extract OBJ_UID and SPECUID from MEC FIBMETATAB
                    def _mget(row, key, default=None):
                        try:
                            return row[key]
                        except Exception:
                            return default
                    
                    obj_uid_val = _mget(meta, 'OBJ_UID', None)
                    if obj_uid_val is not None:
                        try:
                            obj_uid_val = int(obj_uid_val)
                        except Exception:
                            obj_uid_val = None
                    
                    specuid_val = _mget(meta, 'SPECUID', None)
                    if specuid_val is not None:
                        try:
                            specuid_val = int(specuid_val)
                            # Treat -1 or negative values as invalid/missing
                            if specuid_val < 0:
                                specuid_val = None
                        except Exception:
                            specuid_val = None
                    
                    # Obs time - prefer OBMETATAB, fallback to spectrum header
                    obs_date = None
                    obs_mjd = None
                    
                    # Try to get from OBMETATAB first (actual observation date)
                    if obrow is not None:
                        obs_date = _row_get(obrow, ['DATE-OBS'], None)
                        obs_mjd = _row_get(obrow, ['MJD-OBS'], None)
                    
                    # Fallback to spectrum header if not in OBMETATAB
                    if obs_date is None:
                        obs_date = specheader.get('DATE-OBS')
                    if obs_mjd is None:
                        obs_mjd = specheader.get('MJD-OBS') or specheader.get('MJDOBS') or specheader.get('MJD')
                    
                    # Last resort: use current time
                    if obs_mjd is None and obs_date is None:
                        obs_date = datetime.now().isoformat()

                    # Build base metadata (no tides_specid yet - assigned after stacking)
                    metadata = {
                        'TIDES_ID': str(obj_id),
                        'TYPE': 'temp' if self._last_was_temp else 'pending',
                        'OBS_DATE': obs_date,
                        'OBS_MJD': float(obs_mjd) if obs_mjd is not None else None,
                        'SNR': self._json_default(_mget(meta, 'SNR', None)) if _mget(meta, 'SNR', None) is not None else None,
                        'SEEING': self._json_default(_mget(meta, 'SEEING', None)) if _mget(meta, 'SEEING', None) is not None else None,
                        'SKY_BRIGHTNESS': (
                            self._json_default(_mget(meta, 'SKYBRITE', None))
                            if _mget(meta, 'SKYBRITE', None) is not None
                            else (self._json_default(_mget(meta, 'SKY_BRIGHT', None)) if _mget(meta, 'SKY_BRIGHT', None) is not None else None)
                        ),
                        'VERSION': 1,
                        'PIPELINE_ENV': env,
                    }

                    # Add selected primary header fields
                    try:
                        l1_ver = primary_header.get('PROCSOFT')
                    except Exception:
                        l1_ver = None
                    try:
                        file_date = primary_header.get('DATE')
                    except Exception:
                        file_date = None
                    if l1_ver is not None:
                        metadata['L1_PROCSOFT'] = l1_ver
                    if file_date is not None:
                        metadata['FILE_DATE'] = file_date

                    # Add selected OBMETATAB per-spectrum fields (using exact MEC column names)
                    ob_fields = {
                        'OB_TARGET': ['OBJECT'],
                        'OB_RA': ['RA'],
                        'OB_DEC': ['DEC'],
                        'OB_TINT_ELEM_S': ['EXPTIME'],
                        'OB_TINT_SUM_S': ['TEXPTIME'],
                        'OB_START': ['OBSTART'],
                        'OB_MJD_START': ['MJD-OBS'],
                        'OB_MJD_END': ['MJD-END'],
                        'OB_DATE': ['DATE-OBS'],
                        'SPECTRO_PATH': ['PATH'],
                        'OBS_TYPE': ['OBSTYPE'],
                        'BIN_SPEC': ['BINSPECT'],
                        'BIN_SPAT': ['BINSPATL'],
                        'SNR_MEDIAN': ['MEDSNR'],
                        'SNR_MIN': ['MINSNR'],
                        'SNR_MAX': ['MAXSNR'],
                        'L1_PROCESS_DATE': ['PROCDATE'],
                        'OB_ID': ['OBID'],
                        'PI_COI': ['PI-COI'],
                        'OBSERVER': ['OBSERVER'],
                        'NCOMBINE': ['NCOMBINE'],
                        'TELAPSE': ['TELAPSE'],
                        'TMID': ['TMID']
                    }
                    for k, cands in ob_fields.items():
                        val = _row_get(obrow, cands, None)
                        if val is not None:
                            # Coerce numpy scalars to python types via _json_default
                            try:
                                metadata[k] = self._json_default(val)
                            except Exception:
                                metadata[k] = str(val)

                    # Expose a canonical EXPOSURE_TIME_S in metadata when available
                    try:
                        exptime = metadata.get('TEXPTIME')
                        if exptime is not None:
                            # Ensure it’s a plain float/int value in seconds
                            if isinstance(exptime, (list, tuple)):
                                exptime = exptime[0] if exptime else None
                            try:
                                exptime = float(exptime) if exptime is not None else None
                            except Exception:
                                exptime = None
                        if exptime is not None:
                            metadata['EXPOSURE_TIME_S'] = exptime
                    except Exception:
                        pass

                    # Lookup master info for coordinates
                    master_info = self._lookup_master_info(meta)
                    ra_val = None
                    dec_val = None
                    if master_info:
                        ra_val = master_info.get('ra')
                        dec_val = master_info.get('dec')
                        metadata.update({'RA': ra_val, 'DEC': dec_val})
                    else:
                        ra_val, dec_val = self._extract_coords_from_meta(meta)
                        if ra_val is not None and dec_val is not None:
                            metadata.update({'RA': ra_val, 'DEC': dec_val})
                    
                    # Collect spectrum data grouped by OBJ_UID
                    spectrum_data = {
                        'tides_id': obj_id,
                        'obj_uid': obj_uid_val,
                        'specuid': specuid_val,  # SPECUID from MEC FIBMETATAB
                        'wavelength': wave.copy(),
                        'flux': flux.copy(),
                        'ivar': ivar.copy(),
                        'qual': qual.copy(),
                        'metadata': metadata,
                        'fiber_meta': meta,
                        'obrow': obrow,
                        'master_info': master_info,
                        'ra': ra_val,
                        'dec': dec_val,
                        'primary_header': primary_header,
                        'counter': counter
                    }
                    
                    # Group by OBJ_UID for stacking (or use tides_id as fallback key)
                    grouping_key = obj_uid_val if obj_uid_val is not None else f"SINGLE_{obj_id}_{counter}"
                    if grouping_key not in spectra_by_obj_uid:
                        spectra_by_obj_uid[grouping_key] = []
                    spectra_by_obj_uid[grouping_key].append(spectrum_data)
                    
                    if max_spectra is not None and len(spectra_by_obj_uid) >= max_spectra:
                        self.logger.info(f"[data_ingestion] File limit reached ({max_spectra}); stopping parse")
                        break
                        
                except Exception as e:
                    self.logger.error(f"Error parsing spectrum {counter}: {e}")
            
            # PHASE 2: Stack duplicates and assign tides_specid
            self.logger.info(f"Phase 2: Processing {len(spectra_by_obj_uid)} unique OBJ_UID groups...")
            
            # Track counter for each OBJ_UID to generate unique tides_specid when SPECUID is missing
            obj_uid_counters = {}
            
            # Count groups with multiple spectra for debugging
            multi_spec_groups = [k for k, g in spectra_by_obj_uid.items() if len(g) > 1]
            if multi_spec_groups:
                self.logger.info(f"Found {len(multi_spec_groups)} groups with multiple spectra (candidates for stacking)")
                self.logger.info(f"Stacking enabled: {self.stacking_enabled}, Stacking module available: {self.stacking is not None}")
            
            for grouping_key, group in spectra_by_obj_uid.items():
                try:
                    if len(group) > 1 and self.stacking and self.stacking_enabled:
                        # Stack multiple spectra with same OBJ_UID
                        self.logger.info(f"Stacking {len(group)} spectra for OBJ_UID {grouping_key}")
                        
                        # Find the correct tides_id (prefer non-TEMP, or use first)
                        obj_id = None
                        for spec in group:
                            candidate_id = spec['tides_id']
                            if candidate_id is not None:
                                # Check if this is a TEMP id (format: YYYYMMDDXX where XX is counter)
                                id_str = str(candidate_id)
                                if self._temp_prefix and id_str.startswith(self._temp_prefix):
                                    continue  # Skip TEMP ids
                                obj_id = candidate_id
                                break
                        # Fallback to first spectrum's id if all are TEMP
                        if obj_id is None:
                            obj_id = group[0]['tides_id']
                            self.logger.warning(f"All spectra in group {grouping_key} have TEMP tides_id; using {obj_id}")
                        
                        try:
                            # Perform stacking
                            wavelengths = [s['wavelength'] for s in group]
                            fluxes = [s['flux'] for s in group]
                            ivars = [s['ivar'] for s in group]
                            
                            wave_stacked, flux_stacked, ivar_stacked = self.stacking.weighted_median_stack(
                                wavelengths, fluxes, ivars
                            )
                        except Exception as stack_err:
                            self.logger.error(f"Stacking failed for OBJ_UID {grouping_key}: {stack_err}")
                            self.logger.error(f"Falling back to saving individual spectra instead")
                            # Fall through to single-spectrum processing for each
                            for spec in group:
                                self._process_single_spectrum(
                                    spec, obj_uid_counters, spectra_night_dir, fibinfodat, obj_results
                                )
                            continue
                        self.logger.info(f"Stacking successful for OBJ_UID {grouping_key}, move to saving stacked spectrum")
                        # Now assign tides_specid for stacked spectrum
                        import zlib
                        stack_id_str = f"STACK_{grouping_key}_{len(group)}"
                        tides_specid = zlib.crc32(stack_id_str.encode('utf-8')) & 0x7FFFFFFF
                        
                        # Aggregate metadata from all spectra in the stack
                        # Start with first spectrum's metadata
                        stacked_metadata = group[0]['metadata'].copy()
                        
                        # Find earliest OBS_DATE and OBS_MJD
                        earliest_date = None
                        earliest_mjd = None
                        for spec in group:
                            obs_date = spec['metadata'].get('OBS_DATE')
                            obs_mjd = spec['metadata'].get('OBS_MJD')
                            
                            if obs_mjd is not None:
                                if earliest_mjd is None or obs_mjd < earliest_mjd:
                                    earliest_mjd = obs_mjd
                                    earliest_date = obs_date
                        
                        if earliest_date is not None:
                            stacked_metadata['OBS_DATE'] = earliest_date
                        if earliest_mjd is not None:
                            stacked_metadata['OBS_MJD'] = earliest_mjd
                        
                        # Sum exposure times from OBMETATAB TEXPTIME only
                        total_texptime = 0.0
                        for spec in group:
                            texp = spec['metadata'].get('OB_TINT_SUM_S')
                            if texp is not None:
                                try:
                                    total_texptime += float(texp)
                                except Exception:
                                    pass
                        
                        # Update metadata with summed exposure time
                        if total_texptime > 0:
                            stacked_metadata['OB_TINT_SUM_S'] = total_texptime
                        
                        self.logger.info(f"Stacked metadata: DATE-OBS={earliest_date}, TEXPTIME={total_texptime}s, N_spectra={len(group)}")
                        # Save stacked spectrum
                        self._save_and_update_spectrum(
                            tides_specid=tides_specid,
                            tides_id=obj_id,
                            wave=wave_stacked,
                            flux=flux_stacked,
                            ivar=ivar_stacked,
                            qual=None,
                            metadata=stacked_metadata,
                            fiber_meta=group[0]['fiber_meta'],
                            obrow=group[0]['obrow'],
                            master_info=group[0]['master_info'],
                            ra=group[0]['ra'],
                            dec=group[0]['dec'],
                            spectra_night_dir=spectra_night_dir,
                            fibinfodat=fibinfodat,
                            primary_header=group[0]['primary_header'],
                            stacked=True,
                            source_specuids=[s['specuid'] for s in group if s['specuid']]
                        )
                        self.logger.info(f"Stacked spectrum for OBJ_UID {grouping_key}" f" saved with tides_specid {tides_specid}, adding to results")
                        obj_results.append({
                            'tides_id': obj_id,
                            'tides_specid': tides_specid,
                            'filepath': os.path.join(spectra_night_dir, f"{tides_specid}_spectrum.txt")
                        })
                    else:
                        # Single spectrum - use helper method
                        self._process_single_spectrum(
                            group[0], obj_uid_counters, spectra_night_dir, fibinfodat, obj_results
                        )
                        
                except Exception as e:
                    self.logger.error(f"Error processing group {grouping_key}: {e}")
        
        return obj_results

    # --------- Helper for processing single spectrum ---------
    
    def _process_single_spectrum(self, spec, obj_uid_counters, spectra_night_dir, fibinfodat, obj_results):
        """
        Process and save a single spectrum (not stacked).
        Used for lone spectra or when stacking fails.
        """
        obj_id = spec['tides_id']
        
        # Assign tides_specid (prefer SPECUID from MEC)
        if spec['specuid'] is not None and spec['specuid'] > 0:
            tides_specid = spec['specuid']
        else:
            # Generate unique ID based on OBJ_UID + counter
            obj_uid = spec['obj_uid']
            if obj_uid not in obj_uid_counters:
                obj_uid_counters[obj_uid] = 0
            spec_index = obj_uid_counters[obj_uid]
            obj_uid_counters[obj_uid] += 1
            
            # Create unique ID: OBJ_UID * 1000 + spectrum_index
            # This ensures uniqueness and traceability
            if obj_uid is not None:
                tides_specid = (abs(obj_uid) * 1000) + spec_index
            else:
                # Fallback to hash-based ID if OBJ_UID is also missing
                import zlib
                obs_mjd = spec['metadata'].get('OBS_MJD', 0)
                base = f"{obj_id}|{obs_mjd}|{spec['counter']}"
                tides_specid = zlib.crc32(base.encode('utf-8')) & 0x7FFFFFFF
        
        # Save single spectrum
        self._save_and_update_spectrum(
            tides_specid=tides_specid,
            tides_id=obj_id,
            wave=spec['wavelength'],
            flux=spec['flux'],
            ivar=spec['ivar'],
            qual=spec['qual'],
            metadata=spec['metadata'],
            fiber_meta=spec['fiber_meta'],
            obrow=spec['obrow'],
            master_info=spec['master_info'],
            ra=spec['ra'],
            dec=spec['dec'],
            spectra_night_dir=spectra_night_dir,
            fibinfodat=fibinfodat,
            primary_header=spec['primary_header'],
            stacked=False,
            source_specuids=None
        )
        
        obj_results.append({
            'tides_id': obj_id,
            'tides_specid': tides_specid,
            'filepath': os.path.join(spectra_night_dir, f"{tides_specid}_spectrum.txt")
        })

    # --------- Spectrum saving helper ---------
    
    def _save_and_update_spectrum(
        self, tides_specid, tides_id, wave, flux, ivar, qual,
        metadata, fiber_meta, obrow, master_info, ra, dec,
        spectra_night_dir, fibinfodat, primary_header,
        stacked=False, source_specuids=None
    ):
        """
        Save spectrum file, thumbnail, and update tides_spec database.
        Called after stacking decision and tides_specid assignment.
        """
        # Add tides_specid to metadata
        metadata['TIDES_SPECID'] = int(tides_specid)
        
        # Add stacking provenance
        metadata['STACKED'] = stacked
        if stacked and source_specuids:
            metadata['STACKED_SPECUIDS'] = source_specuids
            metadata['N_STACKED'] = len(source_specuids)
        
        # File paths
        spectrum_file = os.path.join(spectra_night_dir, f"{tides_specid}_spectrum.txt")
        metadata_file = os.path.join(spectra_night_dir, f"{tides_id}_metadata.txt")
        thumbnail_file = os.path.join(spectra_night_dir, f"{tides_specid}_thumbnail.png")
        
        # Convert ivar to error for file output
        with np.errstate(divide='ignore', invalid='ignore'):
            fluxerr = 1.0 / np.sqrt(ivar)
            fluxerr[~np.isfinite(fluxerr)] = np.nan
        
        # Save spectrum file
        with open(spectrum_file, 'w') as f:
            f.write("# Wavelength Flux Error Quality\n")
            if qual is not None:
                for w, fl, fe, q in zip(wave, flux, fluxerr, qual):
                    f.write(f"{w} {fl} {fe} {q}\n")
            else:
                # Stacked spectrum - generate quality flags
                for w, fl, fe in zip(wave, flux, fluxerr):
                    q = 0 if np.isfinite(fe) and fe > 0 else 1
                    f.write(f"{w} {fl} {fe} {q}\n")
        
        # Save metadata snapshot
        with open(metadata_file, 'w') as f:
            f.write("# Metadata\n")
            for name in fibinfodat.names:
                try:
                    f.write(f"# {name}: {fiber_meta[name]}\n")
                except Exception:
                    pass
        
        # Generate thumbnail
        self.generate_thumbnail(wave, flux, thumbnail_file)
        
        # Add OBMETATAB fields to metadata
        def _row_get(row, candidates, default=None):
            if row is None:
                return default
            try:
                names = list(getattr(row, 'dtype', {}).names or [])
            except Exception:
                names = []
            name_map = {str(n).lower(): n for n in names}
            for c in candidates:
                key = name_map.get(str(c).lower())
                if key is not None:
                    try:
                        return row[key]
                    except Exception:
                        continue
            return default
        
        ob_fields = {
            'OB_TARGET': ['OBJECT'],
            'OB_RA': ['RA'],
            'OB_DEC': ['DEC'],
            'OB_TINT_ELEM_S': ['EXPTIME'],
            'OB_TINT_SUM_S': ['TEXPTIME'],
            'OB_START': ['OBSTART'],
            'OB_END': ['OBEND'],
            'OB_DATE': ['DATE-OBS'],
            'SPECTRO_PATH': ['PATH'],
            'OBS_TYPE': ['OBSTYPE'],
        }
        for k, cands in ob_fields.items():
            val = _row_get(obrow, cands, None)
            if val is not None:
                try:
                    metadata[k] = self._json_default(val)
                except Exception:
                    metadata[k] = str(val)
        
        # Add L1 processing info
        try:
            l1_ver = primary_header.get('PROCSOFT')
            if l1_ver:
                metadata['L1_PROCSOFT'] = l1_ver
            file_date = primary_header.get('DATE')
            if file_date:
                metadata['FILE_DATE'] = file_date
        except Exception:
            pass
        
        # Ensure BaseTarget and tides_cand
        name_for_bt = master_info.get('name') if master_info else None
        if not name_for_bt:
            name_for_bt = f"TEMP-{tides_id}"
        
        try:
            self._ensure_basetarget(int(tides_id), name_for_bt, ra, dec, fiber_meta=fiber_meta)
        except Exception as e:
            self.logger.debug(f"Ensure BaseTarget failed for {tides_id}: {e}")
        
        try:
            self._ensure_tides_cand(int(tides_id), name_for_bt)
        except Exception as e:
            self.logger.warning(f"Failed to ensure tides_cand({tides_id}): {e}")
        
        # Update tides_spec
        self.update_tides_spec(str(tides_id), tides_specid, metadata, spectrum_file, thumbnail_file)
        
        self.logger.info(f"Saved {'stacked' if stacked else 'single'} spectrum: {spectrum_file}")
    
    # --------- Stacking helper (DEPRECATED - now handled in process_file) ---------
    
    def _perform_stacking(self, spectra_data: List[dict], output_dir: str):
        """
        Check for duplicate OBJ_UIDs and stack matching spectra.
        
        Parameters
        ----------
        spectra_data : List[dict]
            List of spectrum metadata with 'obj_uid', 'wavelength', 'flux', 'error', etc.
        output_dir : str
            Directory where stacked spectra should be saved
        """
        # Group spectra by OBJ_UID
        uid_groups = {}
        for spec in spectra_data:
            uid = spec.get('obj_uid')
            if uid is None:
                continue
            if uid not in uid_groups:
                uid_groups[uid] = []
            uid_groups[uid].append(spec)
        
        # Stack groups with multiple spectra
        for obj_uid, group in uid_groups.items():
            if len(group) < 2:
                continue
            
            self.logger.info(f"Found {len(group)} spectra with OBJ_UID {obj_uid}, performing stacking...")
            
            try:
                stacked_spectra = self.stacking.stack_spectra_by_obj_uid(group, output_dir)
                
                for stacked in stacked_spectra:
                    # Generate unique identifier for stacked spectrum
                    import zlib
                    stack_id_str = f"STACK_{obj_uid}_{len(group)}"
                    tides_specid_stacked = zlib.crc32(stack_id_str.encode('utf-8')) & 0x7FFFFFFF
                    
                    # Save stacked spectrum file
                    stacked_file = os.path.join(output_dir, f"{tides_specid_stacked}_spectrum_stacked.txt")
                    self.stacking.save_stacked_spectrum(stacked, stacked_file, format='txt')
                    
                    # Generate thumbnail for stacked spectrum
                    thumbnail_file = os.path.join(output_dir, f"{tides_specid_stacked}_thumbnail.png")
                    self.generate_thumbnail(
                        stacked['wavelength'],
                        stacked['flux'],
                        thumbnail_file
                    )
                    
                    # Use tides_id from first spectrum in the stack
                    tides_id = group[0]['tides_id']
                    
                    # Create metadata for stacked spectrum
                    stacked_metadata = group[0]['metadata'].copy()
                    stacked_metadata['TIDES_SPECID'] = tides_specid_stacked
                    stacked_metadata['TYPE'] = 'stacked'
                    
                    # Add stacking provenance to additional_info
                    stacked_metadata['STACKED'] = True
                    stacked_metadata['N_STACKED'] = len(group)
                    stacked_metadata['STACKED_SPECUIDS'] = [s.get('tides_specid') for s in group if s.get('tides_specid')]
                    
                    # Update database with stacked spectrum
                    self.update_tides_spec(str(tides_id), stacked_metadata, stacked_file, thumbnail_file)
                    
                    self.logger.info(f"Saved stacked spectrum for OBJ_UID {obj_uid} as {stacked_file}")
                    
            except Exception as e:
                self.logger.error(f"Failed to stack spectra for OBJ_UID {obj_uid}: {e}")

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
          1) Use OBJ_UID to cross-match tides_master.ostd_u_obj_id.
          2) If that fails, use Transients API with OBJ_UID to fetch pk_4most (via 'id' or 'pk_4most'), then query tides_master by pk_4most.
          3) Else, attempt legacy fast path via OBJ_NME → tidestom.tides_cand.
          4) If all above fails, TEMP fallback: <YYYYMMDD><cc>.
        """
        self.logger.debug("[resolve_tides_id] ENTRY: Starting tides_id resolution")
        self._last_was_temp = False

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

        # 1) Prefer OBJ_UID → tides_master.ostd_u_obj_id
        obj_uid = _get_any(meta_row, ['obj_uid', 'ostd_u_obj_id', 'ostd_uobj_id', 'u_obj_id'])
        self.logger.debug(f"[resolve_tides_id] Step 1: Extracted OBJ_UID={obj_uid}")
        if obj_uid is not None:
            self.logger.debug(f"[resolve_tides_id] Step 1: Querying tides_master with ostd_u_obj_id={obj_uid}")
            master = self._query_tides_master(ostd_u_obj_id=obj_uid)
            try:
                if master and isinstance(master.get('tides_id'), (int, np.integer)):
                    tides_id_result = int(master['tides_id'])
                    self.logger.info(f"[resolve_tides_id] ✓ Step 1 SUCCESS: Found tides_id={tides_id_result} via tides_master.ostd_u_obj_id={obj_uid}")
                    return tides_id_result
                else:
                    self.logger.debug(f"[resolve_tides_id] Step 1: tides_master returned no match for ostd_u_obj_id={obj_uid}")
            except Exception as e:
                self.logger.debug(f"[resolve_tides_id] Step 1: Exception during tides_master lookup: {e}")
        else:
            self.logger.debug("[resolve_tides_id] Step 1: OBJ_UID is None, skipping tides_master lookup")

        # 2) Transients API: map OBJ_UID → pk_4most, then tides_master
        self.logger.debug(f"[resolve_tides_id] Step 2: Starting Transients API lookup for OBJ_UID={obj_uid}")
        if obj_uid is not None:
            token = self._get_api_token()
            if token:
                self.logger.debug(f"[resolve_tides_id] Step 2: API token found, querying Transients API")
                try:
                    trans_api.ACCESS_TOKEN = token
                    flt = f"ostd_u_obj_id__exact={int(obj_uid)}"
                    self.logger.debug(f"[resolve_tides_id] Step 2: API filter={flt}")
                    res = trans_api.get_list(flt=flt, limit=10, timeout=15, return_mode="listdict")
                    if isinstance(res, list) and res:
                        self.logger.info(f"[resolve_tides_id] Step 2: Transients API returned {len(res)} results for OBJ_UID {obj_uid}")
                        
                        # Try each result until we find a match in tides_master
                        for idx, cand in enumerate(res):
                            if not isinstance(cand, dict):
                                continue
                            
                            pk_4 = cand.get('pk_4most') or cand.get('id')
                            cand_ostd_u = cand.get('ostd_u_obj_id')
                            cand_name = cand.get('name')
                            
                            self.logger.info(f"[resolve_tides_id] Step 2: Result {idx}: id={pk_4}, ostd_u_obj_id={cand_ostd_u}, name={cand_name}")
                            
                            # Try matching by pk_4most first
                            if pk_4 is not None:
                                self.logger.debug(f"[resolve_tides_id] Step 2: Trying pk_4most={pk_4}")
                                master = self._query_tides_master(pk_4most=pk_4)
                                if master and isinstance(master.get('tides_id'), (int, np.integer)):
                                    tides_id_result = int(master['tides_id'])
                                    self.logger.info(f"[resolve_tides_id] ✓ Step 2 SUCCESS: Found tides_id={tides_id_result} for pk_4most={pk_4}")
                                    return tides_id_result
                                else:
                                    self.logger.debug(f"[resolve_tides_id] Step 2: ✗ No tides_master match for pk_4most={pk_4}")
                            else:
                                self.logger.debug(f"[resolve_tides_id] Step 2: pk_4most is None for result {idx}")
                            
                            # Try matching by name if pk_4most failed
                            if cand_name:
                                self.logger.debug(f"[resolve_tides_id] Step 2: Trying name={cand_name}")
                                master = self._query_tides_master_by_name(cand_name)
                                if master and isinstance(master.get('tides_id'), (int, np.integer)):
                                    tides_id_result = int(master['tides_id'])
                                    self.logger.info(f"[resolve_tides_id] ✓ Step 2 SUCCESS: Found tides_id={tides_id_result} for name={cand_name}")
                                    return tides_id_result
                                else:
                                    self.logger.debug(f"[resolve_tides_id] Step 2: ✗ No tides_master match for name={cand_name}")
                            else:
                                self.logger.debug(f"[resolve_tides_id] Step 2: name is None for result {idx}")
                        
                        self.logger.warning(f"[resolve_tides_id] Step 2: API returned {len(res)} results for OBJ_UID {obj_uid}, but none matched tides_master")
                    else:
                        self.logger.debug(f"[resolve_tides_id] Step 2: Transients API returned empty or invalid results: {res}")
                except Exception as e:
                    self.logger.warning(f"[resolve_tides_id] Step 2: Transients API mapping via OBJ_UID failed: {e}")
            else:
                self.logger.debug("[resolve_tides_id] Step 2: No API token available, skipping Transients API")
        else:
            self.logger.debug("[resolve_tides_id] Step 2: OBJ_UID is None, skipping Transients API")

        # 3) Legacy fast path via OBJ_NME present in tidestom.tides_cand
        self.logger.debug("[resolve_tides_id] Step 3: Trying legacy fast path via OBJ_NME")
        try:
            obj_val = meta_row['OBJ_NME']
            obj_id_fast = int(obj_val)
            self.logger.debug(f"[resolve_tides_id] Step 3: Extracted OBJ_NME={obj_id_fast}")
            if obj_id_fast in valid_ids:
                self.logger.info(f"[resolve_tides_id] ✓ Step 3 SUCCESS: Found tides_id={obj_id_fast} via OBJ_NME in tides_cand")
                return int(obj_id_fast)
            else:
                self.logger.debug(f"[resolve_tides_id] Step 3: OBJ_NME={obj_id_fast} not in preloaded tides_cand")
        except Exception as e:
            self.logger.debug(f"[resolve_tides_id] Step 3: Failed to extract/parse OBJ_NME: {e}")

        # Optional: if other identifiers present, try tides_master directly
        self.logger.debug("[resolve_tides_id] Step 4: Trying other identifiers (ostd_targ_id, pk_4most)")
        ostd_targ  = _get_any(meta_row, ['ostd_targ_id', 'ostd_target_id', 'targ_id'])
        pk_4most   = _get_any(meta_row, ['pk_4most', 'fourmost_id', '4most_id', 'pk_4m'])
        self.logger.debug(f"[resolve_tides_id] Step 4: ostd_targ_id={ostd_targ}, pk_4most={pk_4most}")
        if ostd_targ is not None or pk_4most is not None:
            master = self._query_tides_master(pk_4most=pk_4most, ostd_targ_id=ostd_targ)
            if master and isinstance(master.get('tides_id'), (int, np.integer)):
                tides_id_result = int(master['tides_id'])
                self.logger.info(f"[resolve_tides_id] ✓ Step 4 SUCCESS: Found tides_id={tides_id_result} via other identifiers")
                return tides_id_result
            else:
                self.logger.debug("[resolve_tides_id] Step 4: No match found in tides_master")
        else:
            self.logger.debug("[resolve_tides_id] Step 4: No other identifiers available")

        # 4) TEMP fallback when no resolution was possible (honor allow_temp switch)
        self.logger.debug(f"[resolve_tides_id] Step 5: All resolution methods failed. allow_temp={self._allow_temp}")
        if self._allow_temp:
            self._last_was_temp = True
            temp_id = self._next_temp_id()
            self.logger.warning(f"[resolve_tides_id] Step 5: Falling back to TEMP ID={temp_id}")
            return temp_id
        self._last_was_temp = False
        self.logger.error("[resolve_tides_id] FAILURE: All resolution methods failed and TEMP IDs not allowed. Returning None.")
        return None

    def _extract_coords_from_meta(self, row) -> tuple[Optional[float], Optional[float]]:
        """Read RA/Dec (degrees) directly from FIBMETATAB: OBJ_RA and OBJ_DEC."""
        ra_raw = None
        dec_raw = None
        try:
            ra_raw = row['OBJ_RA']
        except Exception:
            pass
        try:
            dec_raw = row['OBJ_DEC']
        except Exception:
            pass

        if isinstance(ra_raw, (bytes, bytearray)):
            try:
                ra_raw = ra_raw.decode('ascii', errors='ignore')
            except Exception:
                ra_raw = None
        if isinstance(dec_raw, (bytes, bytearray)):
            try:
                dec_raw = dec_raw.decode('ascii', errors='ignore')
            except Exception:
                dec_raw = None

        try:
            ra_val = float(ra_raw) if ra_raw is not None else None
        except Exception:
            ra_val = None
        try:
            dec_val = float(dec_raw) if dec_raw is not None else None
        except Exception:
            dec_val = None

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

    def _ensure_basetarget(self, tides_id: int, name: str, ra: Optional[float], dec: Optional[float], fiber_meta=None):
        """
        Upsert into tom_targets_basetarget. If RA/DEC are missing in basetarget:
          - try to get them from tides_master by tides_id,
          - else from spectrum fiber meta (OBJ_RA/OBJ_DEC),
        then write them. Do not override existing RA/DEC.
        """
        conn = self.connect_to_db()  # tidestom
        if not conn:
            return
        try:
            with conn:
                # Check existing RA/DEC
                existing_ra = None
                existing_dec = None
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT ra, dec FROM tom_targets_basetarget WHERE id = %s", (int(tides_id),))
                        r = cur.fetchone()
                        if r:
                            existing_ra, existing_dec = r[0], r[1]
                except Exception as e:
                    self.logger.debug(f"Fetch existing BaseTarget coords failed for {tides_id}: {e}")

                # Decide what RA/DEC to write only if currently missing
                ra_to_write = None if existing_ra is not None else (float(ra) if ra is not None else None)
                dec_to_write = None if existing_dec is not None else (float(dec) if dec is not None else None)

                # If still missing, try tides_master by tides_id
                if ra_to_write is None or dec_to_write is None:
                    m = self._query_tides_master_by_tides_id(int(tides_id))
                    if m:
                        if ra_to_write is None and m.get('ra') is not None:
                            try: ra_to_write = float(m['ra'])
                            except Exception: pass
                        if dec_to_write is None and m.get('dec') is not None:
                            try: dec_to_write = float(m['dec'])
                            except Exception: pass

                # If still missing, use spectrum fiber meta OBJ_RA/OBJ_DEC
                if (ra_to_write is None or dec_to_write is None) and fiber_meta is not None:
                    ra_s, dec_s = self._extract_coords_from_meta(fiber_meta)
                    if ra_to_write is None and ra_s is not None:
                        ra_to_write = float(ra_s)
                    if dec_to_write is None and dec_s is not None:
                        dec_to_write = float(dec_s)

                # Build dynamic upsert (include ra/dec only when we have values to fill)
                cols_info = self._get_table_columns(conn, 'tom_targets_basetarget')
                cols = []
                vals = []
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
                if 'ra' in cols_info and ra_to_write is not None:
                    cols.append('ra'); vals.append(float(ra_to_write))
                if 'dec' in cols_info and dec_to_write is not None:
                    cols.append('dec'); vals.append(float(dec_to_write))

                required_missing = [
                    c for c, meta in cols_info.items()
                    if not meta['nullable'] and meta['default'] is None and c not in cols
                ]
                for c in required_missing:
                    cols.append(c); vals.append(self._placeholder_for(cols_info[c]))

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

    def _query_tides_master_by_coords(self, ra: float, dec: float, tolerance_arcsec: float = 1.0) -> Optional[Dict[str, Any]]:
        """
        Fallback: query tides_master by coordinates within a small angular tolerance.
        Returns dict with keys: tides_id, name, ra, dec when found, else None.
        """
        if ra is None or dec is None:
            return None
        tol_deg = float(tolerance_arcsec) / 3600.0
        tides_db_name = os.getenv('TIDES_DB_NAME') or 'tides'
        conn = self.connect_to_db(db_name=tides_db_name)
        if not conn:
            return None
        try:
            sql = (
                "SELECT tides_id, name, ra, dec FROM tides_master "
                "WHERE ABS(ra - %s) <= %s AND ABS(dec - %s) <= %s "
                "ORDER BY ABS(ra - %s) + ABS(dec - %s) ASC LIMIT 1"
            )
            params = (float(ra), tol_deg, float(dec), tol_deg, float(ra), float(dec))
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return { 'tides_id': row[0], 'name': row[1], 'ra': row[2], 'dec': row[3] }
                return None
        except Exception as e:
            self.logger.debug(f"tides_master coords lookup failed: {e}")
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _query_tides_master_by_tides_id(self, tides_id: int) -> Optional[Dict[str, Any]]:
        """Query tides_master by tides_id to get name/ra/dec."""
        tides_db_name = os.getenv('TIDES_DB_NAME') or 'tides'
        conn = self.connect_to_db(db_name=tides_db_name)
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT tides_id, name, ra, dec FROM tides_master WHERE tides_id = %s LIMIT 1", (int(tides_id),))
                row = cur.fetchone()
                if row:
                    return {'tides_id': row[0], 'name': row[1], 'ra': row[2], 'dec': row[3]}
                return None
        except Exception as e:
            self.logger.debug(f"tides_master by tides_id lookup failed: {e}")
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _query_tides_master_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Query tides_master by name to get tides_id/ra/dec."""
        if not name:
            return None
        tides_db_name = os.getenv('TIDES_DB_NAME') or 'tides'
        conn = self.connect_to_db(db_name=tides_db_name)
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT tides_id, name, ra, dec FROM tides_master WHERE name = %s LIMIT 1", (str(name),))
                row = cur.fetchone()
                if row:
                    return {'tides_id': row[0], 'name': row[1], 'ra': row[2], 'dec': row[3]}
                return None
        except Exception as e:
            self.logger.debug(f"tides_master by name lookup failed: {e}")
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _ensure_tides_cand(self, tides_id: int, name: Optional[str] = None):
        """Insert tides_id into tidestom.tides_cand if missing; upsert name if column exists."""
        conn = self.connect_to_db()  # default DB (tidestom)
        if not conn:
            return
        try:
            with conn:
                with conn.cursor() as cur:
                    # Discover if 'name' column exists
                    has_name = False
                    try:
                        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='tides_cand' AND column_name='name'")
                        has_name = bool(cur.fetchone())
                    except Exception:
                        has_name = False

                    # Upsert with/without name
                    if has_name and name is not None:
                        cur.execute(
                            """
                            INSERT INTO tides_cand (tides_id, name)
                            VALUES (%s, %s)
                            ON CONFLICT (tides_id)
                            DO UPDATE SET name = EXCLUDED.name
                            """,
                            (int(tides_id), str(name))
                        )
                    else:
                        cur.execute(
                            "INSERT INTO tides_cand (tides_id) VALUES (%s) ON CONFLICT (tides_id) DO NOTHING",
                            (int(tides_id),)
                        )
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
            # Accept OBJ_UID here as well
            ostd_u_obj_id=_get_any(meta_row, ['ostd_u_obj_id', 'ostd_uobj_id', 'u_obj_id', 'obj_uid']),
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
    def update_tides_spec(self, tides_id: str, tides_specid: int, metadata: Dict[str, Any], spectrum_file: str, thumbnail_file: str):
        """
        Upsert into public.tides_spec using the provided schema. Stores full metadata in additional_info
        and maps core fields to dedicated columns. Uses tides_specid as PRIMARY KEY.
        """
        # Prepare values with safe typing
        def _as_float(x):
            try:
                return float(x) if x is not None else None
            except Exception:
                return None

        def _as_int(x):
            try:
                return int(x) if x is not None else None
            except Exception:
                return None

        tides_specid_int = _as_int(tides_specid)
        tides_id_int = _as_int(tides_id or metadata.get('TIDES_ID'))
        sn_type = (metadata.get('TYPE') if metadata.get('TYPE') is not None else None)

        obs_date_raw = metadata.get('OBS_DATE')
        obs_date_dt = None
        try:
            if isinstance(obs_date_raw, datetime):
                obs_date_dt = obs_date_raw
            elif isinstance(obs_date_raw, str):
                # Accept ISO strings
                obs_date_dt = datetime.fromisoformat(obs_date_raw)
        except Exception:
            obs_date_dt = None

        obs_mjd = _as_float(metadata.get('OBS_MJD'))
        snr = _as_float(metadata.get('SNR'))
        seeing = _as_float(metadata.get('SEEING'))
        sky_brightness = _as_float(metadata.get('SKY_BRIGHTNESS'))
        version = _as_int(metadata.get('VERSION'))
        filepath = spectrum_file

        # Build additional_info and include thumbnail path
        additional_info = dict(metadata)
        additional_info['THUMBNAIL'] = thumbnail_file
        additional_info_json = json.dumps(additional_info, default=self._json_default)

        # Use dbutil connection if available; else fallback to module DB
        conn = self._db_connect() or self.connect_to_db()
        if not conn:
            if hasattr(self, "logger"):
                self.logger.error("[ingestion] No DB connection available for tides_spec upsert")
            return

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tides_spec (
                            tides_specid, tides_id, sn_type, obs_date, obs_mjd,
                            snr, seeing, sky_brightness, filepath, version, additional_info
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tides_specid) DO UPDATE SET
                            tides_id = EXCLUDED.tides_id,
                            sn_type = EXCLUDED.sn_type,
                            obs_date = EXCLUDED.obs_date,
                            obs_mjd = EXCLUDED.obs_mjd,
                            snr = EXCLUDED.snr,
                            seeing = EXCLUDED.seeing,
                            sky_brightness = EXCLUDED.sky_brightness,
                            filepath = EXCLUDED.filepath,
                            version = EXCLUDED.version,
                            additional_info = EXCLUDED.additional_info;
                        """,
                        (
                            tides_specid_int, tides_id_int, sn_type, obs_date_dt, obs_mjd,
                            snr, seeing, sky_brightness, filepath, version, additional_info_json
                        )
                    )
            if hasattr(self, "logger"):
                self.logger.info(f"[ingestion] Upserted tides_spec (tides_specid={tides_specid_int}, tides_id={tides_id_int})")
        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.error(f"[ingestion] tides_spec upsert failed: {e}")

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
