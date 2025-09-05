import sys
import logging
from pathlib import Path

from . import QXP_Z, runQXP, L1Spectrum


def main():
    if len(sys.argv) == 1:
        print("Print detailed results for individual files:")
        print("    python3 -m qmostxp <filenames>\n")
        print("Create 4XP output catalog:")
        print("    python3 -m qmostxp <directories>")
    elif Path(sys.argv[1]).is_file():
        # if the first argument is a file, we assume that we process individual
        # files and just display a result table
        qxp = QXP_Z()
        for fname in sys.argv[1:]:
            fp = Path(fname)
            peaks = qxp.run(L1Spectrum(fp), doHelio=False)
            print(fp.stem)
            print("Template   Redshift   CrossCorr   ShiftIndex")
            for peak in peaks:
                print(f"{peak.template.templateId:8d}   {peak.redshift:8.5f}"
                      f"   {peak.crossCorr:9.6f}   {peak.index}")
            print(f"Probabilty on best match is {peaks[0].prob}, "
                  f"FoM is {peaks[0].fom}")
    else:
        logging.basicConfig(level=logging.DEBUG)
        runQXP([Path(d) for d in sys.argv[1:]], Path('.'), index=1)


if __name__ == '__main__':
    main()
