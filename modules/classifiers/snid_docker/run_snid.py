
from astropy.table import Table
from specutils import Spectrum1D
from specutils.manipulation import FluxConservingResampler
import astropy.units as u
import numpy as np
import pysnid
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import logging
import shutil
import pandas as pd
import os

logger = logging.getLogger("startup")

class Params(BaseModel):
    spectrum: str
    wmin: Optional[float] = 4000 ##Done
    wmax: Optional[float] = 9000 ##Done
    zmin: Optional[float] = 0 ##Done
    zmax: Optional[float] = 1.2 ##Done
    emclip: Optional[float] = None #Done
    emwid: Optional[float] = 40 #DONE
    agemin: Optional[float] = -90 #Done
    agemax: Optional[float] = 1000 #Done
    use: object #Done
    usesub: object #Done - MISSING PARAMETER
    avoid: object #Done
    avoidsub: object #Done
    aband: Optional[bool] = False #Done


app = FastAPI()

# Initialize global startup state
snid_startup_complete = False

@app.on_event('startup')
async def startup_event():
    global snid_startup_complete
    try:
        logger.info("Running SNID startup...")

        subtypes = []
        for file in os.listdir('templates-2.0'):
            if file.endswith('lnw'):
                df = pd.read_table(f"templates-2.0/{file}")
                names = df.columns[0].split()
                subtype = names[7]
                if subtype not in subtypes:
                    subtypes.append(subtype)
        if len(subtypes) == 0:
            snid_startup_complete = False
            raise RuntimeError("No Subtypes found, startup may have failed!")
        os.makedirs('/media/snid_template_options', exist_ok=True)
        with open('/media/snid_template_options/subtypes.txt', 'w') as f:
            f.write("\n".join(subtypes))
            snid_startup_complete = True

        logger.info("Startup successful!")
    except Exception as e:
        snid_startup_complete = False
        logger.error(f"Startup failed: {e}")
        raise

@app.get("/health")
async def health():
    file_path = "/media/snid_template_options/subtypes.txt"
    if not snid_startup_complete:
        return JSONResponse(
                status_code=503,
                content={
                    "status": "starting",
                    "file_exists": os.path.exists(file_path)
                    }
                )
    return {
            "status": "ok",
            "file_exists": os.path.exists(file_path),
            }

@app.post("/snid_params/")
def run_snid(params: Params):
    params = params.dict()
    use_type = []
    avoid_type = []

    if len(params['use']) > 0:
        use_type += params['use']

    if len(params['usesub']) > 0:
        use_type += params['usesub']

    if len(params['avoid']) > 0:
        avoid_type += params['avoid']

    if len(params['avoidsub']) > 0:
        avoid_type += params['avoidsub']

    if len(use_type) == 0:
        use_type = None

    if len(avoid_type) == 0:
        avoid_type = None

    file_spec_binned_path='/home/sniduser/snid-5.0/examples'

    # Try to read spectrum file - handle both FITS and simple text formats
    try:
        # First try FITS format
        if params['spectrum'].endswith('.fits') or params['spectrum'].endswith('.fit'):
            logger.info("Reading spectrum as FITS file")
            hdult = Table.read(params['spectrum'], format='fits')
            wl = hdult['WAVE'][0]
            fl = hdult['FLUX'][0]
        else:
            # Try simple text format (wavelength, flux columns)
            logger.info("Reading spectrum as text file")
            # Try reading as space/tab separated text file
            try:
                spectrum_data = Table.read(params['spectrum'], format='ascii')
                # Assume first column is wavelength, second is flux
                if len(spectrum_data.colnames) >= 2:
                    wl = spectrum_data[spectrum_data.colnames[0]]
                    fl = spectrum_data[spectrum_data.colnames[1]]
                else:
                    raise ValueError("Text file must have at least 2 columns (wavelength, flux)")
            except Exception as text_error:
                logger.error(f"Failed to read as text file: {text_error}")
                # Try pandas as fallback
                df = pd.read_csv(params['spectrum'], sep=r'\s+', header=None)
                if len(df.columns) >= 2:
                    wl = df.iloc[:, 0].values
                    fl = df.iloc[:, 1].values
                else:
                    raise ValueError("Text file must have at least 2 columns (wavelength, flux)")
        
        logger.info(f"Successfully read spectrum with {len(wl)} wavelength points")
        
    except Exception as e:
        logger.error(f"Failed to read spectrum file {params['spectrum']}: {e}")
        raise ValueError(f"Could not read spectrum file: {e}")

    # create a Spectrum1D object for specutils
    spec = Spectrum1D(spectral_axis=wl* u.AA , flux=fl* u.Unit('erg cm-2 s-1 AA-1') )

    # binned wavelength array, at 15 Angstroms
    wl_smooth = np.arange(wl[0], wl[-1], 15) * u.AA

    # binned flux array
    fluxcon = FluxConservingResampler()
    fl_smooth = fluxcon(spec, wl_smooth)

    # make an ascii file of the binned spectrum to run pysnid
    data_spec = np.column_stack([fl_smooth.spectral_axis.value, fl_smooth.flux.value])
    np.savetxt(f"{file_spec_binned_path}/binned.ascii",
               data_spec, fmt=['%.2f','%.4e'])

    #run pysnid
    snidres = pysnid.run_snid(f"{file_spec_binned_path}/binned.ascii",
                              get_results=False,lbda_range=
                              [params['wmin'],params['wmax']], redshift_bounds=
                              [params['zmin'],params['zmax']], phase_range=
                              [params['agemin'], params['agemax']], emwid=
                              params['emwid'], usetype = use_type, avoidtype=
                              avoid_type, emclip=params['emclip'],
                              aband=params['aband'])

    #test = snidres.get_results()
    shutil.move(snidres, '/snid_api_runs/test.h5')
# this will create a file named file_spec_binned_ascii+'_snid.h5'
    test = pysnid.snid.SNIDReader.from_filename('/snid_api_runs/test.h5')
    print(test.results)
    df = test.results.copy()

    # Replace non-finite values with None
    df = df.replace([np.inf, -np.inf], np.nan).where(pd.notnull(df), None)
    df = df[['sn', 'typing', 'subtyping', 'lap', 'rlap', 'z', 'zerr', 'age']]

    return {"success": True, "data": {"file_path": "/snid_api_runs/test.h5" ,
                                      "table": df.to_dict(orient='records')[:10]}}

import tempfile

@app.post("/classify")
async def classify_spectrum(file: UploadFile = File(...), 
                          wmin: float = 4000,
                          wmax: float = 9000,
                          zmin: float = 0,
                          zmax: float = 1.2,
                          agemin: float = -90,
                          agemax: float = 1000,
                          emclip: float = None,
                          emwid: float = 40,
                          aband: bool = False,
                          use: str = "",
                          usesub: str = "",
                          avoid: str = "",
                          avoidsub: str = ""):
    """
    Classify spectrum endpoint that accepts file uploads and converts to snid_params format
    """
    # Save uploaded file to the snid_api_runs directory
    upload_path = f'/snid_api_runs/{file.filename}'
    
    try:
        logger.info(f"Starting classification for file: {file.filename}")
        
        # Write uploaded file
        content = await file.read()
        with open(upload_path, 'wb') as f:
            f.write(content)
        
        logger.info(f"File saved to: {upload_path}")
        
        # Parse comma-separated string parameters into lists
        use_list = [x.strip() for x in use.split(',') if x.strip()] if use else []
        usesub_list = [x.strip() for x in usesub.split(',') if x.strip()] if usesub else []
        avoid_list = [x.strip() for x in avoid.split(',') if x.strip()] if avoid else []
        avoidsub_list = [x.strip() for x in avoidsub.split(',') if x.strip()] if avoidsub else []
        
        logger.info(f"Parsed parameters - use: {use_list}, usesub: {usesub_list}, avoid: {avoid_list}, avoidsub: {avoidsub_list}")
        
        # Create params object matching the Params model
        params = Params(
            spectrum=upload_path,
            wmin=wmin,
            wmax=wmax,
            zmin=zmin,
            zmax=zmax,
            agemin=agemin,
            agemax=agemax,
            emclip=emclip,
            emwid=emwid,
            aband=aband,
            use=use_list,
            usesub=usesub_list,
            avoid=avoid_list,
            avoidsub=avoidsub_list
        )
        
        logger.info("Created params object, calling run_snid")
        
        # Call the existing snid_params logic
        result = run_snid(params)
        
        logger.info("SNID processing completed successfully")
        return result
        
    except Exception as e:
        logger.error(f"Error in classify_spectrum: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Classification failed: {str(e)}"}
        )
        
    finally:
        # Cleanup uploaded file
        try:
            if os.path.exists(upload_path):
                os.remove(upload_path)
                logger.info(f"Cleaned up file: {upload_path}")
        except OSError as e:
            logger.warning(f"Failed to cleanup file {upload_path}: {e}")

#Remove age_flag, type, grade

#Show the first match of different type
