import functools
import numpy as np

from astropy.table import QTable


class QXPSpectrum:
    """Base class for both observed and template spectrum

    This class features a linear or logarithmic wavelength and an
    optimized fast fourier transform of the flux.

    Parameters
    ----------

    wavelength : :class:`astropy.units.Quantity`
        Wavelength array [Å]

    flux : array-like
        Flux array

    flux_error : array-like
        Optional flux error array

    Attributes
    ----------

    wavelength : :class:`astropy.units.Quantity`
        Wavelength array [Å]

    flux : array-like
        Flux array

    flux_error : array-like
        Optional flux error array

    """
    def __init__(self, wavelength, flux, flux_error=None):
        if len(wavelength) != len(flux):
            raise ValueError('Lengths of wavelength and flux must match')
        self.wavelength = wavelength
        self.flux = flux
        self.flux_error = flux_error

    def rebin(self, wavelength):
        """Rebin the spectrum to another wavelength binning

        This is done using linear interpolation. Bins outside of the
        old range will be set to 0.0.

        Parameters
        ----------

        wavelength :  :class:`astropy.units.Quantity`
            New wavelength array [Å]

        Returns
        -------
        :class:`.QXPSpectrum`
            Spectrum with new binning

        Examples
        --------

        Rebin a sample spectrum to logarithmic wavelength::

            >>> import numpy as np
            >>> import astropy.units as u
            >>> wavelength = np.linspace(3000., 9000., 10) * u.Angstrom
            >>> flux = np.array([0., 0., 1., 2., 4., 3., 1., 2., 1., 0.])
            >>> spec = QXPSpectrum(wavelength, flux)
            >>> loglambda = np.linspace(3.5, 3.9, 8) * u.dex(u.Angstrom)
            >>> print(spec.rebin(loglambda).as_table())
                wavelength            flux
              dex(Angstrom)
            ------------------ ------------------
                           3.5                0.0
             3.557142857142857                0.0
            3.6142857142857143 0.6893704194987365
            3.6714285714285713 1.5568405381898145
            3.7285714285714286 3.0891352928216937
            3.7857142857142856  3.329534543217756
            3.8428571428571425 1.1031110727646685
                           3.9 1.5749103803032882

        """
        return QXPSpectrum(wavelength,
                           np.interp(wavelength, self.wavelength,
                                     self.flux, left=0.0, right=0.0))

    def subspectrum(self, lower, upper):
        '''Extract a subspectrum with given limits


        Parameters
        ----------
        lower : :class:`astropy.units.Quantity`
            Lower wavelength limit [Å]

        upper : :class:`astropy.units.Quantity`
            Upper wavelength limit [Å]


        Returns
        -------
        :class:`.QXPSpectrum`
            New flux spectrum within the given limits

        Examples
        --------

        Take the "greeen" subspectrum or a simple (constant) spectrum::

            >>> import numpy as np
            >>> import astropy.units as u
            >>> wavelength = np.arange(3000., 9000., 1.) * u.Angstrom
            >>> flux = np.ones(wavelength.shape)
            >>> spec = QXPSpectrum(wavelength, flux)
            >>> print(spec.subspectrum(5200*u.Angstrom, 7200*u.Angstrom).as_table())
            wavelength flux
             Angstrom
            ---------- ----
                5200.0  1.0
                5201.0  1.0
                   ...  ...
                7199.0  1.0
                7200.0  1.0
            Length = 2001 rows

        '''  # noqa: E501
        wl_slice = slice(np.searchsorted(self.wavelength, lower, 'left'),
                         np.searchsorted(self.wavelength, upper, 'right'))
        wavelength = self.wavelength[wl_slice]
        flux = self.flux[wl_slice]
        if self.flux_error is not None:
            flux_error = self.flux_error[wl_slice]
        else:
            flux_error = None
        return QXPSpectrum(wavelength, flux, flux_error)

    def __len__(self):
        return len(self.wavelength)

    def __sub__(self, other):
        return QXPSpectrum(self.wavelength, self.flux - other, self.flux_error)

    def __isub__(self, other):
        self.flux[:] -= other
        return self

    def __truediv__(self, other):
        '''Divide this spectrum

        '''
        if self.flux_error is None:
            return QXPSpectrum(self.wavelength, self.flux / other)
        else:
            return QXPSpectrum(self.wavelength, self.flux / other,
                               self.flux_error / other)

    def __itruediv__(self, other):
        '''Divide this spectrum in-place
        '''
        self.flux[:] /= other
        if self.flux_err is not None:
            self.flux_err[:] /= other
        return self

    @functools.cached_property
    def fluxFFT(self):
        """Fast Fourier transform of the flux

        This interprets NaN values as zeros.

        Examples
        --------

        Get the Fast Fourier transform of a simple spectrum::

            >>> import numpy as np
            >>> import astropy.units as u
            >>> wavelength = np.linspace(300., 310., 8) * u.nm
            >>> flux = np.array([0., 0., 1., 2., 4., 3., 1., 0.])
            >>> spec = QXPSpectrum(wavelength, flux)
            >>> spec.fluxFFT
            array([ 1.375     +0.j        , -0.94194174+0.08838835j,
                    0.25      -0.125j     , -0.05805826+0.08838835j,
                    0.125     +0.j        , -0.05805826-0.08838835j,
                    0.25      +0.125j     , -0.94194174-0.08838835j])

        """
        use = np.isfinite(self.flux)
        f = np.where(use, self.flux, 0)
        # Alternative: interpolate NaN values
        # f = np.interp(self.wavelength, self.wavelength[use], self.flux[use])
        return np.fft.fft(f) / len(self.flux)

    def crossCorr(self, other):
        """Cross correlate one spectrum with another

        Both spectra must have the same wavelength.

        Return a reduced size of array centred around zero shift.

        Parameters
        ----------

        other : :class:`.QXPSpectrum`
            1D input spectrum

        Returns
        -------
        :class:`numpy.ndarray`
            Array of length len(spec) with cross correlation values.
            The central value (cc[len(spec)-1)//2]) of the array
            corresponds to a zero shift.

        Examples
        --------

        Cross correlate a simple spectrum with itself::

            >>> import numpy as np
            >>> import astropy.units as u
            >>> wavelength = np.linspace(300., 310., 8) * u.nm
            >>> flux = np.array([0., 0., 1., 2., 4., 3., 1., 0.])
            >>> spec = QXPSpectrum(wavelength, flux)
            >>> spec.crossCorr(spec)
            array([0.625, 1.75 , 3.125, 3.875, 3.125, 1.75 , 0.625])

        """
        if not np.array_equal(self.wavelength, other.wavelength):
            raise ValueError('wavelength must match for cross correlation')

        # multiply by conj. of fourier transform of t_plate spectrum
        # take real part of inverse FFT for cross correlation.
        crC = np.fft.ifft(other.fluxFFT * self.fluxFFT.conj()).real

        n = len(self) // 2
        # From FFT docs:
        # crC[0] contains the zero frequency term,
        # crC[1:n//2] contains the positive-frequency terms,
        # crC[n//2 + 1:] contains the negative-frequency terms, in
        #                increasing order starting from the most negative
        #                frequency.
        return np.concatenate((crC[n + 1:], crC[:n])) * len(self.flux)

    def as_table(self):
        """Return the spectrum as a  :class:`astropy.table.QTable`.

        The columns are `wavelength`, `flux`, and (if existent)
        `error`.

        Examples
        --------

        Print out a small spectrum as table::

            >>> import numpy as np
            >>> import astropy.units as u
            >>> wavelength = np.linspace(300., 310., 9) * u.nm
            >>> flux = np.array([0., 0., 1., 2., 4., 3., 1.,0.,0.])
            >>> spec = QXPSpectrum(wavelength, flux)
            >>> spec.as_table()
            <QTable length=9>
            wavelength   flux
                nm
             float64   float64
            ---------- -------
                 300.0     0.0
                301.25     0.0
                 302.5     1.0
                303.75     2.0
                 305.0     4.0
                306.25     3.0
                 307.5     1.0
                308.75     0.0
                 310.0     0.0

        """
        tbl = QTable({'wavelength': self.wavelength,
                      'flux': self.flux})
        if self.flux_error is not None:
            tbl.add_column(self.flux_error, name='error')

        return tbl
