import numpy as np

import astropy
from astropy.table import QTable
import astropy.time
import astropy.coordinates
import astropy.units as u

from . import QXPSpectrum


class L1Spectrum(QXPSpectrum):
    """Read in 4MOST L1 data from a fits file and producing correct
    structure for QXP

    Parameters
    ----------

    fname : :class:`astropy.fits.HDUList`, :class:`pathlib.Path` or :class:`str`
        Fits file location to read in

    Examples
    --------

    Read the test file provided with the package::

        >>> from qmostxp import test_data
        >>> fpath = test_data / 'qmost_00033300-2847587_20200913_100025_LJ1.fits'
        >>> spec = L1Spectrum(fpath)
        >>> spec.wavelength
        <Quantity [3700.  , 3700.25, 3700.5 , ..., 9500.  ] Angstrom>
        >>> spec.flux
        <Quantity [ 2.0513637e-17, ..., 4.7812651e-17] erg / (Angstrom cm2 s)>
        >>> spec.time.mjd
        59105.881944445
        >>> spec.skycoord
        <SkyCoord (ICRS): (ra, dec) in deg
            (50.85032829, 68.77431464)>
    """  # noqa: E501
    def __init__(self, fname):
        self.table = QTable.read(fname)

        # Convert missing numbers into NaNs instead of using a
        # masked array.
        if astropy.version.major >= 5:
            for name in ('FLUX', 'ERR_FLUX'):
                if isinstance(self.table[name], astropy.utils.masked.Masked):
                    self.table[name] = self.table[name].filled(np.nan)

        default_unit = {
            'WAVE': u.Angstrom,
            'FLUX': u.Unit('10**-17 Angstrom-1 cm-2 erg s-1'),
            'ERR_FLUX': u.Unit('10**-17 Angstrom-1 cm-2 erg s-1'),
        }
        for name, column in self.table.columns.items():
            if name in default_unit and column.unit is None:
                column.unit = default_unit[name]

    @property
    def wavelength(self):
        if len(self.table) == 1:
            return self.table['WAVE'][0]
        else:
            return self.table['WAVE']

    @property
    def flux(self):
        if len(self.table) == 1:
            return self.table['FLUX'][0]
        else:
            return self.table['FLUX']

    @property
    def flux_error(self):
        if len(self.table) == 1:
            return self.table['ERR_FLUX'][0]
        else:
            return self.table['ERR_FLUX']

    @property
    def meta(self):
        """Metadata for the spectrum; :class:`dict`"""
        return self.table.meta

    @property
    def time(self):
        """Observation date/time of the spectrum; :class:`astropy.time.Time`"""
        return astropy.time.Time(self.table.meta['TMID'], format='mjd')

    @property
    def skycoord(self):
        """Sky coordinates associated with this spectrum;
        :class:`astropy.coordinates.SkyCoord`"""
        return astropy.coordinates.SkyCoord(
            ra=float(self.table.meta['RA']) * u.rad,
            dec=float(self.table.meta['DEC']) * u.rad)
