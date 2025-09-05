"""These classes are executed to preprocessed the L1 spectrum. Objects
of these classes are callable (i.e. they provide a :meth:`__call__`
method. The modules are called just with :class:`.QXPSpectrum` as
argument, and return a :class:`.QXPSpectrum`, so that they can be
pipelined.

This example shows how the preprocessing steps can be pipelined (this
is done in :meth:`.QXP_Z.processSpectrum`). For demonstration, a rough
constant spectrum is used::

    >>> import numpy as np
    >>> import astropy.units as u
    >>> from qmostxp.spectrum import QXPSpectrum
    >>> # Create a sample uniform spectrum
    >>> wavelength = np.arange(3500., 9500., 1.) * u.Angstrom
    >>> spectrum = QXPSpectrum(wavelength, np.ones(wavelength.shape))
    >>> # Create a small preprocessing pipeline
    >>> pipeline = [InvCorrection(), CosineBell()]
    >>> # Run the pipeline
    >>> for step in pipeline:
    ...     spectrum = step(spectrum)
    >>> # Print some results
    >>> print(spectrum.as_table())
    wavelength          flux
     Angstrom
    ---------- ---------------------
        3736.0                   0.0
        3737.0  0.002327417021292472
           ...                   ...
        8839.0 0.0006813671905506325
        8840.0                   0.0
    Length = 5105 rows

As seen in this example, the wavelength range may change during the
pipeline processing.

"""

import numpy as np
import scipy.ndimage
import astropy.units as u

from . import QXPSpectrum
from .VacuumFromAir import air_to_vac
# from specutils.utils.wcs_utils import air_to_vac


class MaskBadData:
    """Mask out bad data

    Good data have the following criteria:
      * flux between minVal and maxVal
      * flux error greater than zero

    The masking is done by setting the flux resp. the flux error
    within the range to NaN.

    Parameters
    ----------
    minFlux :  :class:`astropy.units.Quantity`
        Minimal flux value to be accepted as good value [erg/(s cm² Å)]

    maxFlux :  :class:`astropy.units.Quantity`
        Maximal flux value to be accepted as good value [erg/(s cm² Å)]

    """
    def __init__(self, minFlux, maxFlux):
        self.minFlux = minFlux
        self.maxFlux = maxFlux

    def __call__(self, spectrum):
        flux = spectrum.flux.copy()
        flux[(flux < self.minFlux) | (flux > self.maxFlux)] = np.nan
        if spectrum.flux_error is not None:
            flux_error = spectrum.flux_error.copy()
            flux_error[flux_error <= 0] = np.nan
        else:
            flux_error = None
        return QXPSpectrum(spectrum.wavelength, flux, flux_error)


class MaskWavelengthRange:
    """Mask a certain wavelength range (default the telluric absorption range)

    The masking is done by setting the flux within the range to
    NaN. The flux error remains unchanged.

    Parameters
    ----------
    minLambda : :class:`astropy.units.Quantity`
        Minimal wavelength to mask [Å]

    maxLambda : :class:`astropy.units.Quantity`
        Maximal wavelength to mask [Å]

    """
    def __init__(self, minLambda=7580 * u.Angstrom,
                 maxLambda=7730 * u.Angstrom):
        self.minLambda = minLambda
        self.maxLambda = maxLambda

    def __call__(self, spectrum):
        i_range = np.searchsorted(spectrum.wavelength, (self.minLambda,
                                                        self.maxLambda))
        flux = spectrum.flux.copy()
        flux[i_range[0] + 1:i_range[1]] = np.nan
        return QXPSpectrum(spectrum.wavelength, flux, spectrum.flux_error)


class InvCorrection:
    """Set approximate relative flux 'calibration' by a fixed polynomial

    Parameters
    ----------
    coefficients : :class:`list`
        Polynomial coefficients. The default was obtained using
        GAMA standard stars.

    Examples
    --------

    Show a few fixpoints::

        >>> import numpy as np
        >>> import astropy.units as u
        >>> from qmostxp.spectrum import QXPSpectrum
        >>> # Create a sample uniform spectrum
        >>> wavelength = np.arange(3500., 9000.) * u.Angstrom
        >>> spectrum = QXPSpectrum(wavelength, np.ones(wavelength.shape))
        >>> # Create the pipeline
        >>> invcorr = InvCorrection()
        >>> # process the pipeline
        >>> spectrum = invcorr(spectrum)
        >>> print(spectrum.as_table()[::1000])
        wavelength        flux
         Angstrom
        ---------- ------------------
            3500.0  2.983116733888395
            4500.0 1.5102338305422687
            5500.0 1.0909731186337328
            6500.0 0.8714284688023074
            7500.0 0.7420569470733671
            8500.0 0.6881274730683128

    """
    def __init__(self,
                 coefficients=[-3.406, 22.175, -49.208, 55.037, -23.441]):
        self.polynomial = np.polynomial.Polynomial(coefficients)

    def __call__(self, spectrum):
        invCorrection = self.polynomial(spectrum.wavelength.to_value('µm'))
        return spectrum / invCorrection


class SetMinimumError:
    """Set minimum error to avoid anomalously low errors,

    This step requires an input spectrum containing an flux error
    column.  In the output spectrum, this is replaced by the larger of
    the flux error and its median filtered value multiplied by 0.7.

    Parameters
    ----------
    medwidth : :class:`int`
        number of bins for median filter for replacement value

    fac : :class:`float`
        Multiplication factor of replacement value

    """
    def __init__(self, medwidth=13, fac=0.7):
        self.medwidth = medwidth
        self.fac = fac

    def __call__(self, spectrum):
        flux_error = spectrum.flux_error.copy()
        flux_error[np.isnan(flux_error)] = 0
        adjustedError = (
            self.fac
            * scipy.ndimage.median_filter(flux_error.value, self.medwidth)
            * flux_error.unit
        )
        np.maximum(spectrum.flux_error, adjustedError, flux_error)
        return QXPSpectrum(spectrum.wavelength, spectrum.flux, flux_error)


class NormalizeByError:
    """Divide by the square of the error spectrum.

    This weighting was justified by Saunders et al.
    :adsabs:`2004SPIE.5492..389S` as appropriate for effectively
    minimizing χ² when finding the peak.

    This step requires an input spectrum containing an error spectrum.

    """
    def __call__(self, spectrum):
        return spectrum / spectrum.flux_error**2


class NormalizeStdDev:
    """Normalise a spectrum by dividing by mean absolute deviation.

    Iterate for clipping if clipvalue keyword is set.

    Justfication for this comes from :adsabs:`2014MNRAS.441.2440B`.

    Parameters
    ----------

    clipvalue : :class:`float`
        Value to clip/replace normalized flux

    quantile : :class:`float`
        The quantile of absolute values over which to ignore. Set
        to 1.0 (default) to use all values.

    """
    def __init__(self, clipvalue=25, quantile=1.0):
        self.clipvalue = clipvalue
        self.quantile = quantile

    def __call__(self, spectrum):
        flux = spectrum.flux
        use = np.abs(flux) <= np.nanquantile(np.abs(flux), self.quantile)

        flux = flux / np.nanmean(np.abs(flux[use]))

        # Iterate mean deviation clipping each time until convergence within
        # a tolerance of 0.01.
        for _ in range(10000):
            success = np.nanmax(np.abs(flux)) <= self.clipvalue + 0.01
            np.clip(flux, -self.clipvalue, self.clipvalue, flux)
            if success:
                break
            flux /= np.nanmean(np.abs(flux[use]))

        return QXPSpectrum(spectrum.wavelength, flux)


class SubtractPolynomial:
    """Subtract a polynomial from the spectrum

    A fourth-order polynomial is fitted to the spectrum iteratively,
    with a maximum of 15 iterations. After each iteration, points more
    than 3.5σ away from the best-fitting curve are rejected. The final
    fourth-order polynomial is then subtracted from the spectrum.

    Justfication for this comes from :adsabs:`2014MNRAS.441.2440B`.

    Parameters
    ----------

    maxiter : :class:`int`
        Maximum number of iterations to perform for sigma rejection.

    deg : :class:`int`
        Degree of the fitting polynomials.

    sigma : :class:`float`
        Sigma cut value

    """
    def __init__(self, maxiter=15, deg=4, sigma=3.5):
        self.maxiter = maxiter
        self.deg = deg
        self.sigma = sigma

    def __call__(self, spectrum):
        x = spectrum.wavelength.to_value(u.Angstrom)
        y = spectrum.flux.value

        pts = np.isfinite(x) & np.isfinite(y)  # Start with all finite values
        for _ in range(self.maxiter):
            poly = np.polynomial.Polynomial.fit(x[pts], y[pts],
                                                self.deg, domain=[])
            residuals = poly(x[pts]) - y[pts]
            newpts = np.abs(residuals / residuals.std()) < self.sigma
            if newpts.all():
                break
            pts[pts] = newpts
            if not pts.any():
                break

        return spectrum - poly(x) * spectrum.flux.unit


class SmoothingFilter:
    """Apply a smoothing filter

    A median kernel filter of width 51 is applied to the result of the
    first step. On each end, the 25 edge points were given a median
    value of those points. This median-filtered spectrum is smoothed
    using a trapezium filter, by applying two boxcar smooths of width
    121 and 21. This low-pass spectrum is then subtracted from the
    result of the first step to obtain the HPF spectrum.

    Justfication for this comes from :adsabs:`2014MNRAS.441.2440B`.

    Parameters
    ----------

    medWidth : :class:`int`
        number of bins for filtering kernal for median smoothing.
        Setting to None will disable this filter.

    smoothWidth : :class:`int`
        number of bins for first box smoothing kernal
        Setting to None will disable this filter.

    runningWidth : :class:`int`
        number of bins for second box smoothing kernal
        Setting to None will disable this filter.

    """
    def __init__(self, medWidth=51, smoothWidth=121, runningWidth=21):
        self.medWidth = medWidth
        if smoothWidth is not None:
            self.smoothKernel = np.ones(smoothWidth) / smoothWidth
        else:
            self.smoothKernel = None
        if runningWidth is not None:
            self.runningKernel = np.ones(runningWidth) / runningWidth
        else:
            self.runningKernel = None

    def __call__(self, spectrum):
        fluxSmooth = spectrum.flux.value
        if self.medWidth is not None:
            fluxSmooth = scipy.ndimage.median_filter(fluxSmooth, self.medWidth)
        if self.smoothKernel is not None:
            fluxSmooth = np.convolve(self.smoothKernel,
                                     fluxSmooth, mode='same')
        if self.runningKernel is not None:
            fluxSmooth = np.convolve(self.runningKernel,
                                     fluxSmooth, mode='same')
        return spectrum - fluxSmooth * spectrum.flux.unit


class CosineBell:
    """Apply a cosine bell at the edges of the spectrum

    Set end points smoothly to zero between :math:`stLambda+os1 …
    stLambda+os2` and :math:`endLambda-os1 … endLambda-os2` using
    a cosine bell taper (apodization; :adsabs:`1998PASP..110..934K`). The
    resulting spectrum is limited to the range :math:`stLambda+os1
    … endLambda-os1`

    Parameters
    ----------

    stLambda : :class:`astropy.units.Quantity`
        min wavelength range to fit over [Å].

    endLambda : :class:`astropy.units.Quantity`
        max wavelength range to fit over [Å].

    os1 : :class:`astropy.units.Quantity`
        length to restrict above stLambda resp. below endLambda for
        final specrum [Å].

    os2 : :class:`astropy.units.Quantity`
        length abost stLambda resp. below endLambda to start with
        cosine bell [Å].


    Examples
    --------

    Have a look at the bell::

        >>> import numpy as np
        >>> import astropy.units as u
        >>> from qmostxp.spectrum import QXPSpectrum
        >>> # Create a sample uniform spectrum
        >>> wavelength = np.arange(3500., 9000., 10.) * u.Angstrom
        >>> spectrum = QXPSpectrum(wavelength, np.ones(wavelength.shape))
        >>> # Create the pipeline
        >>> cosbell = CosineBell(3540*u.Angstrom, 8840*u.Angstrom)
        >>> # process the pipeline
        >>> spectrum = cosbell(spectrum)
        >>> print(spectrum.as_table()[:6])
        wavelength         flux
         Angstrom
        ---------- -------------------
            3550.0                 0.0
            3560.0 0.09549150281252627
            3570.0  0.3454915028125263
            3580.0  0.6545084971874737
            3590.0  0.9045084971874737
            3600.0                 1.0
        >>> print(spectrum.as_table()[-6:])
        wavelength         flux
         Angstrom
        ---------- -------------------
            8780.0                 1.0
            8790.0  0.9045084971874737
            8800.0  0.6545084971874737
            8810.0 0.34549150281252633
            8820.0 0.09549150281252633
            8830.0                 0.0

    """
    def __init__(self, stLambda=3726 * u.Angstrom,
                 endLambda=8850 * u.Angstrom,
                 os1=10 * u.Angstrom, os2=60 * u.Angstrom):
        self.stLambda = stLambda
        self.endLambda = endLambda
        self.os1 = os1
        self.os2 = os2

    def __call__(self, spectrum):
        spec = spectrum.subspectrum(self.stLambda + self.os1,
                                    self.endLambda - self.os1)

        # Set end points smoothly to zero between stLambda+os2 and
        # stLambda+os1 using a cosine bell taper (apodization; Kurtz &
        # Mink 1998).
        n = np.searchsorted(spec.wavelength,
                            self.stLambda + self.os2, 'right')
        if n > 0:
            spec.flux[:n] *= 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, n)))

        # Set end points smoothly to zero between endLambda-os2 and
        # endLambda-os1
        n = np.searchsorted(spec.wavelength,
                            self.endLambda - self.os2, 'left') - len(spec)
        if n < 0:
            spec.flux[n:] *= 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, -n)))

        return spec


class AirToVac:
    """Convert air wavelength to vacuum wavelength

    Examples
    --------

    Convert a sample spectrum to vacuum wavelength::

        >>> import numpy as np
        >>> import astropy.units as u
        >>> from qmostxp.spectrum import QXPSpectrum
        >>> # Create a sample uniform spectrum
        >>> wavelength = np.arange(3500., 9000., 10.) * u.Angstrom
        >>> spectrum = QXPSpectrum(wavelength, np.arange(float(len(wavelength))))
        >>> # Create the pipeline
        >>> airtovac = AirToVac()
        >>> # process the pipeline
        >>> spectrum = airtovac(spectrum)
        >>> print(spectrum.as_table()[:6])
            wavelength     flux
             Angstrom
        ------------------ ----
        3501.0012760406157  0.0
          3511.00384946758  1.0
        3521.0064241233795  2.0
        3531.0089999940774  3.0
         3541.011577065939  4.0
        3551.0141553254366  5.0

    """  # noqa: E501

    def __call__(self, spectrum):
        return QXPSpectrum(air_to_vac(spectrum.wavelength),
                           spectrum.flux,
                           spectrum.flux_error)


class Rebin:
    """Rebin the spectrum to a new wavelength range

    This is used to rebin to logarithmic scale.

    Parameters
    ----------
    wavelength : :class:`astropy.units.Quantity`
        New wavelength array [Å] or [dex(Å)].

    Examples
    --------

    Rebin an example spectrum to logarithmic wavelengths::

        >>> import numpy as np
        >>> import astropy.units as u
        >>> from qmostxp.spectrum import QXPSpectrum
        >>> # Create a sample uniform spectrum
        >>> wavelength = np.arange(3500., 9000., 10.) * u.Angstrom
        >>> spectrum = QXPSpectrum(wavelength, np.arange(float(len(wavelength))))
        >>> # Create the pipeline
        >>> logwavelength = np.arange(3.54, 4., 0.002) * u.dex(u.Angstrom)
        >>> airtovac = Rebin(logwavelength)
        >>> # process the pipeline
        >>> spectrum = airtovac(spectrum)
        >>> print(spectrum.as_table()[:6])
            wavelength            flux
          dex(Angstrom)
        ------------------ ------------------
                      3.54                0.0
                     3.542                0.0
        3.5439999999999996                0.0
        3.5459999999999994  1.560790910644175
         3.547999999999999 3.1833815431984687
         3.549999999999999  4.813603193858558

    """  # noqa: E501
    def __init__(self, wavelength):
        self.wavelength = wavelength

    def __call__(self, spectrum):
        return spectrum.rebin(self.wavelength)
