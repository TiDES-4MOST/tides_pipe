# run_qxp.py

import numpy as np
import astropy.units as u
import pandas as pd
import qmostxp
from pathlib import Path
import argparse
import warnings
import glob
from datetime import datetime
from pydantic import BaseModel
from fastapi import FastAPI

warnings.simplefilter("ignore", category=RuntimeWarning) # This will suppress RuntimeWarning
warnings.simplefilter("ignore", category=UserWarning)  # This will suppress RankWarning

class Params(BaseModel):
    file: str
    medWidth: int | None = 4*51
    smoothWidth: int | None = 4*121
    runningWidth: int | None = 4*21
    stLambda: float | None = 3500
    endLambda: float | None = 9500
    num: int | None = 4
    verbose: bool | None = False
    export_csv: bool | None = False

app = FastAPI()

#def proces_file(file, medWidth, smoothWidth, runningWidth, stLambda, endLambda, num, verbose, export_csv):
@app.post('/qxp/')
def process_file(params: Params):

    (
        file,
        medWidth,
        smoothWidth,
        runningWidth,
        stLambda,
        endLambda,
        num,
        verbose,
        export_csv,
    ) = params.values()


    file = Path(file)
    spec = qmostxp.L1Spectrum(file)

    print(f'Running QXP on {file}...')

    qxp = qmostxp.QXP_Z(
        #tempFile='/home/gdimit/qxp/templates/filtered-templates.fits',
        tempFile='/Users/georgios/test_stuff/test_qxp/templates/filtered-templates.fits',

        templateNumbers=np.r_[23:29, 40:50],
        highZ=True,
        stLambda=stLambda * u.Angstrom,
        endLambda=endLambda * u.Angstrom,
        medWidth=medWidth,
        smoothWidth=smoothWidth,
        runningWidth=runningWidth
    )

    result_peaks = qxp.run(spec, num=num, doHelio=False)

    if verbose:
        print('TemplateID   TemplateType   Redshift    crossCorr')
        for peak in result_peaks:
            print(f"{peak.template.templateId:8d}   {peak.template.ttype}"
                  f"   {peak.redshift:8.5f}   {peak.crossCorr:9.6f}")

    if export_csv and result_peaks:
        best_peak = result_peaks[0]

        data = {
            "File": str(file),
            "Template_ID": best_peak.template.templateId,
            "Template_Type": best_peak.template.ttype,
            "redshift": best_peak.redshift,
            "redshift_error": best_peak.redshiftErr,
            "Probability": best_peak.prob,
            "Figure_of_Merit": best_peak.fom
        }

        df = pd.DataFrame([data])
        if export_csv is not False:
            #write_header = not Path(export_csv).exists()
            df.to_csv(export_csv, mode='a', header=write_header, index=False)


def main(file_pattern, medWidth, smoothWidth, runningWidth, stLambda, endLambda, num, verbose, export_csv):
    files = glob.glob(file_pattern)

    if not files:
        print(f"No files found for pattern {file_pattern}.")
        return

    for file in files:
        process_file(file, medWidth, smoothWidth, runningWidth, stLambda, endLambda, num, verbose, export_csv)

#if __name__ == "__main__":
#    parser = argparse.ArgumentParser(description="Run QXP on a spectrum file.")
#    parser.add_argument("file", type=str, help="Path to the FITS file(s).")
#    parser.add_argument("--medWidth", type=int, default=4*51, help="Median filter width.")
#    parser.add_argument("--smoothWidth", type=int, default=4*121, help="Smoothing filter width.")
#    parser.add_argument("--runningWidth", type=int, default=4*21, help="Running filter width.")
#    parser.add_argument("--stLambda", type=int, default=3500, help="Lower bound of the wavelength range to fit over in Angstroms.")
 #   parser.add_argument("--endLambda", type=int, default=9500, help="Upper bound of the wavelength range to fit over in Angstroms.")
#    parser.add_argument("--num", type=int, default=4, help="Number of cross-correlation peaks to identify.")
#    parser.add_argument("--verbose", action="store_true", help="Print detailed output on screen.")
#    parser.add_argument("--export_csv", action="store_true", help="If specified, export a timestamp CSV with results.")


#    args = parser.parse_args()
#    if args.export_csv:
#        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#        export_csv = f"qxp_results_{timestamp}.csv"
#    else:
#        export_csv = None

 #   main(args.file, args.medWidth, args.smoothWidth, args.runningWidth, args.stLambda, args.endLambda, args.num, args.verbose, export_csv)
