import os
import random
import json
import psycopg2
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
from astropy.io import fits
from datetime import datetime
from shutil import copyfile
from modules import db

# Load environment variables from .env file if running directly
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not available, rely on environment variables being set
    pass

# Configuration
DELIVERIES_DIR = os.getenv("DATA_DELIVERIES_PATH", "./deliveries")
STATIC_DIR = os.getenv("DATA_STATIC_PATH", "./static/plots") 
SPECTRA_DIR = os.getenv("DATA_SPECTRA_PATH", "./spectra")

def connect_to_db():
    """Connects to the PostgreSQL database using db.py configuration."""
    try:
        # Load credentials using db.py, falling back to None config (environment variables)
        creds = db.load_creds(None)
        conn = db.connect(creds)
        return conn
    except Exception as e:
        print(f"Failed to connect to the database: {e}")
        return None

def get_mock_objects(conn):
    """Fetches the 1000 mock objects from the tides_cand table."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT tides_id FROM tides_cand LIMIT 1000")
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        print(f"Failed to fetch mock objects: {e}")
        return []

def insert_into_tides_spec(conn, obj_name, metadata, spectra_file_path, thumbnail_file_path):
    """Inserts or updates data in the tides_spec table."""
    try:
        metadata['THUMBNAIL'] = thumbnail_file_path
        cursor = conn.cursor()
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
        conn.commit()
        print(f"Inserted/Updated tides_spec for object {obj_name}")
    except Exception as e:
        print(f"Failed to insert/update tides_spec for object {obj_name}: {e}")
        conn.rollback()

def batch_insert_into_tides_spec(conn, batch_data):
    """Batch inserts data into the tides_spec table."""
    try:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO tides_spec (tides_id, qmost_id, type, obs_date, obs_mjd, snr, seeing, sky_brightness, filepath, version, additional_info)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, batch_data)
        conn.commit()
        print(f"Batch inserted/updated tides_spec for {len(batch_data)} objects")
    except Exception as e:
        print(f"Failed to batch insert/update tides_spec: {e}")
        conn.rollback()

def insert_into_global_classification(conn, obj_name):
    """Inserts mock classifications into the pipeline_classification_global table."""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pipeline_classification_global (tides_id, sn_type, subclass, probability, version, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            obj_name,  # tides_id
            random.choice(["Type Ia", "Type Ib", "Type Ic", "Type II"]),  # sn_type
            random.choice(["Normal", "Broad-lined", "91bg-like"]),  # subclass
            round(random.uniform(0.5, 1.0), 2),  # probability
            "mock_version",  # version
            "Mock classification for testing"  # notes
        ))
        conn.commit()
        print(f"Inserted mock classification for object {obj_name}")
    except Exception as e:
        print(f"Failed to insert mock classification for object {obj_name}: {e}")
        conn.rollback()

def generate_thumbnail(wavelength, flux, thumbnail_file):
    """Generates a thumbnail plot for the spectrum and saves it as a PNG file."""
    try:
        if not os.path.exists(STATIC_DIR):
            os.makedirs(STATIC_DIR)

        thumbnail_path = os.path.join(STATIC_DIR, os.path.basename(thumbnail_file))

        plt.figure(figsize=(4, 3))  # Thumbnail size
        plt.plot(wavelength, flux, color='blue', linewidth=0.5)
        plt.xlabel('Wavelength [Å]')
        plt.ylabel('Flux')
        plt.title('Spectrum Thumbnail')
        plt.tight_layout()
        plt.savefig(thumbnail_path, dpi=100)
        plt.close()
        print(f"Generated thumbnail: {thumbnail_path}")
    except Exception as e:
        print(f"Failed to generate thumbnail: {e}")

def main():
    conn = connect_to_db()
    if not conn:
        return

    # Fetch mock objects from tides_cand
    mock_objects = get_mock_objects(conn)
    if not mock_objects:
        print("No mock objects found in tides_cand.")
        conn.close()
        return

    # List all FITS files in the spectra directory
    available_spectra_files = [f for f in os.listdir(DELIVERIES_DIR) if f.endswith(".fits")]
    if not available_spectra_files:
        print("No FITS files found in the spectra directory.")
        conn.close()
        return

    # Simulate processing spectral files
    for obj_name in mock_objects:
        # Pick a random FITS file from the available files
        random_spectra_file = random.choice(available_spectra_files)
        copyfile(os.path.join(DELIVERIES_DIR, random_spectra_file), os.path.join(SPECTRA_DIR, random_spectra_file))
        spectra_file_path = os.path.join(SPECTRA_DIR, random_spectra_file)
        thumbnail_file_path = os.path.join(STATIC_DIR, f"{obj_name}_thumbnail.png")

        # Read FITS file data
        try:
            data = fits.getdata(spectra_file_path)
            wavelength = data['WAVE']
            flux = data['FLUX']
        except Exception as e:
            print(f"Failed to read FITS file {spectra_file_path}: {e}")
            continue

        # Mock metadata
        metadata = {
            "TIDES_ID": obj_name,
            "QMOST_ID": random.randint(100000, 999999),
            "TYPE": random.choice(["SN", "AGN", "Galaxy"]),
            "OBS_DATE": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "OBS_MJD": round(random.uniform(59000, 60000), 2),
            "SNR": round(random.uniform(10, 50), 2),
            "SEEING": round(random.uniform(0.5, 2.0), 2),
            "SKY_BRIGHTNESS": round(random.uniform(18, 22), 2),
            "VERSION": 1,
        }

        # Insert into tides_spec
        insert_into_tides_spec(conn, obj_name, metadata, spectra_file_path, thumbnail_file_path)

        # Insert mock classification
        insert_into_global_classification(conn, obj_name)

        # Generate thumbnail
        generate_thumbnail(wavelength, flux, thumbnail_file_path)

    conn.close()

if __name__ == "__main__":
    main()