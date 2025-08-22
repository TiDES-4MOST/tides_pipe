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

class DataIngestion(Module):
    def __init__(self, config):
        self.config = config

    def process_night(self, night):
        # Use paths from the config file
        deliveries_dir = self.config['data_paths']['deliveries_dir']
        spectra_dir = self.config['data_paths']['spectra_dir']
        archive_dir = self.config['data_paths']['archive_dir']

        night_dir = os.path.join(deliveries_dir, night)
        spectra_night_dir = os.path.join(spectra_dir, night)
        archive_night_dir = os.path.join(archive_dir, night)

        if not os.path.exists(night_dir):
            self.logger.info(f"No data found for night {night}.")
            return

        files = [f for f in os.listdir(night_dir) if f.endswith(".fits")]
        if not files:
            self.logger.info(f"No new files found for night {night}.")
            return

        if not os.path.exists(spectra_night_dir):
            os.makedirs(spectra_night_dir)

        # Write "FALSE" to DONE.txt at the start
        signal_file = os.path.join(spectra_night_dir, "DONE.txt")
        with open(signal_file, 'w') as f:
            f.write("FALSE\n")

        obj_names = []
        for file in files:
            file_path = os.path.join(night_dir, file)
            self.logger.info(f"Processing file: {file}")
            try:
                obj_names.extend(self.process_file(file_path, spectra_night_dir))
            except Exception as e:
                self.logger.error(f"Error processing {file}: {e}")

        self.archive_files(night_dir, archive_night_dir)
        self.set_done(True)
        return obj_names

    def process_file(self, file_path, spectra_night_dir):
        self.logger.info(f"Parsing data from {file_path}")
        obj_names = []

        # Check if test mode is enabled
        if self.config['data_ingestion'].get('test', False):
            try:
                tides_db_conn = self.connect_to_db("tides_db")
                if not tides_db_conn:
                    self.logger.error("Failed to connect to tides_db for retrieving tides_id")
                    return obj_names

                try:
                    cursor = tides_db_conn.cursor()
                    cursor.execute("SELECT tides_id FROM tides_cand ORDER BY RANDOM() LIMIT 1")
                    tides_id = cursor.fetchone()[0]
                    obj_name = tides_id  # Use tides_id as obj_name in test mode
                    obj_names.append(obj_name)

                    spectrum_file = os.path.join(spectra_night_dir, f"{obj_name}_spectrum.txt")
                    thumbnail_file = os.path.join(spectra_night_dir, f"{obj_name}_thumbnail.png")

                    # Save dummy spectrum data
                    with open(spectrum_file, 'w') as f:
                        f.write("# Wavelength Flux\n")
                        for w in range(4000, 8000, 10):  # Dummy wavelength range
                            f.write(f"{w} {random.uniform(0, 100)}\n")  # Random flux values

                    # Generate and save dummy thumbnail
                    self.generate_thumbnail(range(4000, 8000, 10), [random.uniform(0, 100) for _ in range(400)], thumbnail_file)

                    self.logger.info(f"Saved dummy spectrum to {spectrum_file} and thumbnail to {thumbnail_file}")
                except Exception as e:
                    self.logger.error(f"Failed to retrieve tides_id or process file in test mode: {e}")
                finally:
                    tides_db_conn.close()
            except Exception as e:
                self.logger.error(f"Error processing FITS file in test mode: {e}")
        else:
            # Normal mode: Parse FITS file contents
            with fits.open(file_path, memmap=False) as hdulist:
                fibinfodat = hdulist['FIBMETATAB'].data
                specdata = hdulist[2].data
                specheader = hdulist[2].header
                wave = specheader['1CRVL1'] + (1 + np.arange(0, float(specheader['TDIM1'].strip(' ').strip('(').strip(')')))) - specheader['1CRPX1'] * specheader['1CDLT1']
                
                for counter, (flux, fluxerr, qual) in enumerate(zip(specdata['FLUX'], specdata['ERR'], specdata['QUAL'])):
                    try:
                        meta = fibinfodat[counter]
                        obj_name = meta['OBJ_NME']
                        obj_names.append(obj_name)
                        
                        spectrum_file = os.path.join(spectra_night_dir, f"{obj_name}_spectrum.txt")
                        metadata_file = os.path.join(spectra_night_dir, f"{obj_name}_metadata.txt")
                        thumbnail_file = os.path.join(spectra_night_dir, f"{obj_name}_thumbnail.png")
                        
                        # Save spectrum data
                        with open(spectrum_file, 'w') as f:
                            f.write("# Wavelength Flux Error Quality\n")
                            for w, fl, fe, q in zip(wave, flux, fluxerr, qual):
                                f.write(f"{w} {fl} {fe} {q}\n")
                        
                        # Save metadata
                        with open(metadata_file, 'w') as f:
                            f.write("# Metadata\n")
                            for name in fibinfodat.names:
                                f.write(f"# {name}: {meta[name]}\n")
                        
                        # Generate and save thumbnail
                        self.generate_thumbnail(wave, flux, thumbnail_file)
                        
                        self.logger.info(f"Saved spectrum to {spectrum_file}, metadata to {metadata_file}, and thumbnail to {thumbnail_file}")

                        # Update tides_spec with metadata
                        self.update_tides_spec(obj_name, meta, spectrum_file, thumbnail_file)
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

    def update_tides_spec(self, obj_name, metadata, spectra_file_path, thumbnail_file_path):
        """
        Add or update tides_spec for the given object, including the thumbnail filepath in the additional_info JSON field.
        """
        tides_db_conn = self.connect_to_db("tides_db")
        if not tides_db_conn:
            self.logger.error("Failed to connect to tides_db for updating tides_spec")
            return

        try:
            metadata['THUMBNAIL'] = thumbnail_file_path  # Local static path

            cursor = tides_db_conn.cursor()
            cursor.execute("""
                INSERT INTO tides_spec (tides_id, qmost_id, type, obs_date, obs_mjd, snr, seeing, sky_brightness, filepath, version, additional_info)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (qmost_id) DO UPDATE SET
                    obs_date = EXCLUDED.obs_date,
                    filepath = EXCLUDED.filepath,
                    version = EXCLUDED.version,
                    additional_info = EXCLUDED.additional_info
            """, (
                metadata['TIDES_ID'],  # tides_id
                metadata['QMOST_ID'],  # qmost_id
                metadata['TYPE'],      # type
                metadata['OBS_DATE'],  # obs_date
                metadata['OBS_MJD'],   # obs_mjd
                metadata['SNR'],       # snr
                metadata['SEEING'],    # seeing
                metadata['SKY_BRIGHTNESS'],  # sky_brightness
                spectra_file_path,     # filepath
                metadata['VERSION'],   # version
                json.dumps(metadata)   # additional_info
            ))
            tides_db_conn.commit()
            self.logger.info(f"Updated tides_spec for object {obj_name}")
        except Exception as e:
            self.logger.error(f"Failed to update tides_spec for object {obj_name}: {e}")
        finally:
            tides_db_conn.close()

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
