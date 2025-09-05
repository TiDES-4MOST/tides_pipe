import itertools
import logging
import numpy as np
from datetime import datetime

import astropy.units as u
from astropy.io import fits

from . import L1Spectrum, QXP_Z, makeDXU


def runQXP(dirs, dirOut=None, index=1, range_=None):
    """Run the QXP redshift pipeline for many files, create catalog

    If an output directory was given, the output file name is created
    like ``Qmost_{index}_{yyyymmdd}_l2qxp.fits``.

    .. note:: This function uses Python logging, which should be
        properly initialized for production use.

    Parameters
    ----------

    dirs : list of :class:`pathlib.Path`
        List of directories to search for "LJ1.fits" input files.

    dirOut : :class:`pathlib.Path`
        Output directory. If not given, no file will be written.

    index : :class:`int`
        Batch number (???); used to create the output file name

    range_ : pair of :class:`int`
        Range that limits the list of input files. If not given, all
        input files are used.

    Returns
    -------
    :class:`astropy.io.fits.HDUList`
        Created FITS output catalog and provenance HDU list

    Examples
    --------

    Run the pipeline with the test data provided with the package::

        >>> from astropy.table import QTable
        >>> from qmostxp import test_data
        >>> res = runQXP([test_data])
        >>> print(res[1].name)
        PHASE3CATALOGUE
        >>> print(QTable.read(res[1]))
        TARGET_UID      TARGET_CNAME     TARGET_RA ...  z4CCSig  z4T_ID     z4Type
                                            deg    ...
        ---------- --------------------- --------- ... --------- ------ --------------
                 2 QMOST00033103-2820362      0.88 ...      4.28     32            QSO
                 1 QMOST00033300-2847587      0.89 ...      4.08     40 Passive_galaxy
        >>> print(res[2].name)
        PHASE3PROVENANCE
        >>> print(QTable.read(res[2]))
                              PROV
        -----------------------------------------------
        qmost_00033103-2820362_20200913_100030_LJ1.fits
        qmost_00033300-2847587_20200913_100025_LJ1.fits

    """  # noqa: E501
    testSpec = sorted(itertools.chain(*(d.glob("*LJ1.fits") for d in dirs)))

    if range_ is not None:
        testSpec = testSpec[range_[0]:range_[1]]

    catalog = makeDXU.makeCatalogTable(len(testSpec))

    qxp = QXP_Z(templateNumbers=np.r_[1:14, 15:22, 29:33, 39:47])

    file_info = makeDXU.FileInfo()

    for filepath, row in zip(testSpec, catalog):
        hdulist = fits.open(filepath)
        spec = L1Spectrum(hdulist)
        if spec.meta.get('TRG_UID') in (None, -1):
            logging.warning(f'{filepath.name}: no TRG_UID; unprocessed')
            continue

        file_info.add(hdulist[0].header, filepath)

        row['TARGET_UID'] = spec.meta['TRG_UID']
        row['TARGET_CNAME'] = spec.meta['CNAME']
        row['TARGET_RA'] = spec.meta['RA'] * u.deg
        row['TARGET_DEC'] = spec.meta['DEC'] * u.deg

        logging.debug(f'{filepath.name}: ')

        peaks = qxp.run(spec, doHelio=False)

        logging.debug(f'z={peaks[0].redshift:.2f}'f'/{peaks[0].prob:.4f}')

        row['zBest'] = peaks[0].redshift
        row['zBestErr'] = peaks[0].redshiftErr
        row['zBestCCSig'] = peaks[0].crossCorr
        row['zBestFOM'] = peaks[0].fom
        row['zBestProb'] = peaks[0].prob
        row['zBestT_ID'] = peaks[0].template.templateId
        row['zBestType'] = peaks[0].template.ttype
        for i in [1, 2, 3]:
            row[f'z{i+1}'] = peaks[i].redshift
            row[f'z{i+1}Err'] = 1e-4
            row[f'z{i+1}CCSig'] = peaks[i].crossCorr
            row[f'z{i+1}T_ID'] = peaks[i].template.templateId
            row[f'z{i+1}Type'] = peaks[i].template.ttype

    del catalog[np.where(catalog['TARGET_UID'] == '')[0]]

    primaryHeader = file_info.makePrimaryHDU()
    provenance = file_info.makeProvenance()

    hduList = makeDXU.makeDXU(primaryHeader, catalog, provenance)
    if dirOut is not None:
        date = datetime.now()
        hduList.writeto(
            dirOut / f'Qmost_{index}_{date:%Y%m%d}_l2qxp.fits',
            overwrite=True)

    logging.info(f'QXP completed, {len(testSpec)} total, {len(catalog)} '
                 f'science, {sum(catalog["zBestProb"] > 0.95)} good')

    return hduList
