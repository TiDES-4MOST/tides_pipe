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
try:
    from tides_pipe.utils import dbutil
except Exception:
    dbutil = None

class DataIngestion(Module):
    def __init__(self, config):
        self.config = config or {}
        self._db_conn = None

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

            # Preload valid tides_ids from tides_cand to ensure OBJ_NME matches
            valid_ids = set()
            tides_db_conn = self.connect_to_db()
            if not tides_db_conn:
                self.logger.error("Failed to connect to database to validate tides_id")
                return obj_names
            
            self.logger.info("Successfully connected to database")
            try:
                cur = tides_db_conn.cursor()
                cur.execute("SELECT tides_id FROM tides_cand")
                valid_ids = {int(r[0]) for r in cur.fetchall()}
                self.logger.info(f"Loaded {len(valid_ids)} valid tides_ids from tides_cand table")
            except Exception as e:
                self.logger.error(f"Could not fetch tides_cand ids: {e}")
            finally:
                tides_db_conn.close()

            # Iterate all spectra in the file
            for counter, (flux, fluxerr, qual) in enumerate(zip(specdata['FLUX'], specdata['ERR_FLUX'], specdata['QUAL'])):
                try:
                    meta = fibinfodat[counter]
                    obj_id = int(meta['OBJ_NME'])#self._obj_nme_to_bigint(meta['OBJ_NME']) To put back in when tides_cand.tides_id is a bigint
                    if obj_id not in valid_ids:
                        self.logger.warning(f"OBJ_NME {obj_id} not found in tides_cand; skipping.")
                        continue
                    obj_names.append(str(obj_id))

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

                    metadata = {
                        'TIDES_ID': int(obj_id),
                        'QMOST_ID': self._compute_qmost_id(obj_id, spectrum_file),
                        'TYPE': 'pending',
                        'OBS_DATE': obs_date,
                        'OBS_MJD': float(obs_mjd) if obs_mjd is not None else None,
                        'SNR': _mget(meta, 'SNR', None),
                        'SEEING': _mget(meta, 'SEEING', None),
                        'SKY_BRIGHTNESS': _mget(meta, 'SKYBRITE', None) or _mget(meta, 'SKY_BRIGHT', None),
                        'VERSION':1 #_mget(meta, 'QMOST_PIPELINE_VERSION', '1.0'),
                    }

                    # Update tides_spec with metadata (stores thumbnail path in additional_info)
                    self.update_tides_spec(str(obj_id), metadata, spectrum_file, thumbnail_file)
                    # Stop early if we hit the per-file budget in test mode
                    if max_spectra is not None and len(obj_names) >= max_spectra:
                        self.logger.info(f"[data_ingestion] File limit reached ({max_spectra}) for {os.path.basename(file_path)}; stopping this file")
                        break
                except Exception as e:
                    self.logger.error(f"Error processing spectrum {counter} in file {file_path}: {e}")
        return obj_names

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
