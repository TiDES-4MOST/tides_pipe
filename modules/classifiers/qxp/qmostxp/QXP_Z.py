import itertools

import numpy as np
import astropy.units as u
import astropy.coordinates
import astropy.time

from . import QXPTemplate, CrossCorrInfo, CalculateCertainty
from .preprocess import (
    MaskWavelengthRange, MaskBadData, SetMinimumError,
    NormalizeByError, NormalizeStdDev, SubtractPolynomial,
    SmoothingFilter, CosineBell, AirToVac, Rebin
)


class QXP_Z:
    """4MOST redshifting tool for 1D spectra

    Based on redshfiting tool originaly developed by Ivan Baldry in
    IDL :adsabs:`2014MNRAS.441.2440B`.  Repurposed and expanded in R
    by Luke Davies and Leon Drygala, and then converted to Python and
    restructured by Ole Streicher. Takes a 1D spectrum and perfoms a
    Fourier cross corellation with a template spectra set. Returns the
    best fit redshifts and probabilities.

    Parameters
    ----------

    tempFile : :class:`pathlib.Path` or :class:`str`
        Path to file containing spectral template data

    templateNumbers : array of :class:`int`
        template numbers to use in fitting

    oversample : :class:`int`
        wavelength oversampling rate

    highZ : :class:`bool`
        run code in highZ mode - increases corrSize

    minFlux : :class:`astropy.units.Quantity`
        minmum flux value to reject cross correlations
        [erg / (Å cm² s)]

    maxFlux : :class:`astropy.units.Quantity`
        maximum flux value to reject cross correlations
        [erg / (Å cm² s)]

    mask7600Abs : :class:`bool`
        mask out sky absoption region at 7600 Å (spectrum is simply
        masked between 7580 Å and 7730 Å)

    medWidth : :class:`int`
        pixel width of filtering kernal for SmoothingFilter

    smoothWidth : :class:`int`
        pixel width of smoothing kernal for SmoothingFilter

    runningWidth : :class:`int`
        pixel width of smoothing kernal for SmoothingFilter

    stLambda : :class:`astropy.units.Quantity`
        lower bound of the wavelength range to fit over [Å]

    endLambda : :class:`astropy.units.Quantity`
        upper bound of the wavelength range to fit over [Å]


    Examples
    --------

    Calculate the redshifts for the test file provided with the package::

        >>> from qmostxp import L1Spectrum, test_data
        >>> fpath = test_data / 'qmost_00033300-2847587_20200913_100025_LJ1.fits'
        >>> spec = L1Spectrum(fpath)
        >>> qxp = QXP_Z()
        >>> peaks = qxp.run(spec, doHelio=False)
        >>> for peak in peaks:
        ...    print(f"{peak.template.templateId:8d}   {peak.redshift:8.5f}"
        ...          f"   {peak.crossCorr:9.6f}   {peak.index}")
              40    0.15001   10.156115   3143
              32    1.70583    4.877185   21615
              32    1.71312    4.414509   21673
              40    0.41307    4.078361   7616
              40    0.00594    4.047027   237
        >>> print(f"Probabilty on best match is {peaks[0].prob}, "
        ...      f"FoM is {peaks[0].fom}")
        Probabilty on best match is 0.74434..., FoM is 4.07403...

    """  # noqa: E501
    def __init__(self, tempFile=None,
                 templateNumbers=np.r_[1:49],
                 oversample=5, highZ=True,
                 minFlux=-1e4 * u.Unit('erg/(s*cm^2*Angstrom)'),
                 maxFlux=1e6 * u.Unit('erg/(s*cm^2*Angstrom)'),
                 mask7600Abs=True,
                 medWidth=51, smoothWidth=121, runningWidth=21,
                 stLambda=3726 * u.Angstrom, endLambda=8850 * u.Angstrom):
        # oversample the SDSS log wavelength gap, default is a factor of 5
        sdssGap = 0.0001

        self.gap = sdssGap / oversample * u.dex(u.one)
        # set up new lambda scale to rebin spectrum and templates to
        # and rebin filtered spectrum ensuring zero outside range.
        self.logLambda = np.arange(3.3 if not highZ else 3.0,
                                   4.0,
                                   self.gap.value) * u.dex(u.Angstrom)

        # load rebinned template data
        self.templates = QXPTemplate.read(self.logLambda,
                                          templateNumbers=templateNumbers,
                                          fname=tempFile)

        self.preprocess = [
            MaskBadData(minFlux, maxFlux),
            # InvCorrection(),
            SubtractPolynomial(),
            SmoothingFilter(medWidth, smoothWidth, runningWidth),
            CosineBell(stLambda, endLambda),
            SetMinimumError(),
            NormalizeByError(),
            NormalizeStdDev(),
            AirToVac(),
            Rebin(self.logLambda),
        ]

        if mask7600Abs:
            self.preprocess.insert(0, MaskWavelengthRange())

    def run(self, specRaw, doHelio=True, num=5):
        """Calc redshift estimation for for 1D spectra

        .. note:: Assumes input is in air wavelengths!

        Parameters
        ----------

        specRaw : :class:`.L1Spectrum`
            Spectrum coming from the L1 pipeline

        doHelio : :class:`bool`
            perform helocentric correction. If True you must provide
            RA,DEC,UTMJD, longitude, latitude and altitue in the specRaw
            structure.

        num : :class:`int`
            number of crosscorrelation peaks to identify

        Returns
        -------
        list of : :class:`.CrossCorrPeak`
            List of cross correlation peaks, starting with the best one.
            The first (best) :class:`.CrossCorrPeak` additionally has
            the attributes

              * **prob** : :class:`float`
                   Calculated probability of best-fit redshift
              * **fom** : :class:`float`
                   Figures of merit of best-fit redshift

        """
        spec = self.processSpectrum(specRaw)
        meanadNorm = np.mean(np.abs(spec.flux))
        # MAD will be like
        # from scipy.stats iimport median_abs_deviation
        # meanadNorm = median_abs_deviation(spec.flux)
        rmsNorm = np.sqrt(np.mean(spec.flux**2))

        if doHelio:
            paranal = astropy.coordinates.EarthLocation.of_site(
                'Paranal Observatory')
            helioVel = specRaw.skycoord.radial_velocity_correction(
                'heliocentric', specRaw.time, paranal)
        else:
            helioVel = 0 * u.m / u.s

        # Get the "num" highest cross correllation peaks
        peaks = list(itertools.islice(self.peaks(spec, helioVel), max(num, 4)))

        # calculate FOM and Probability
        peaks[0].prob, peaks[0].fom = CalculateCertainty(
            rmsNorm, meanadNorm, [p.crossCorr for p in peaks])

        peaks[0].redshiftErr = self.CalculateRedshiftErr(peaks)

        return peaks[:num]

    def processSpectrum(self, spectrum):
        """Preprocess an input spectrum

        Apply all pre processing steps to the spectrum and return the
        resulting spectrum.

        Parameters
        ----------
        spectrum : :class:`.QXPSpectrum`
            Input spectrum

        Returns
        -------
        :class:`.QXPSpectrum`
            Resulting spectrum

        """
        for step in self.preprocess:
            spectrum = step(spectrum)
        return spectrum

    def peaks(self, spectrum, helioVel=0 * u.m / u.s):
        """Calculate the cross correllation of the spectrum

        Iterate over all peaks in the cross correllation with all
        templates.

        Parameters
        ----------

        spectrum : :class:`.QXPSpectrum`
            (Preprocessed) input spectrum

        helioVel : :class:`astropy.units.Quantity`
            Heliocentric velocity [m/s].


        Returns
        -------

        Iterator of : :class:`.CrossCorrPeak`
            Iterate over all cross correlation peaks, starting with
            the best one.

        """
        ccinfo = (CrossCorrInfo(spectrum, template, self.gap, helioVel)
                  for template in self.templates)

        # extract all peaks in normalised cross correlation value
        # across all templates
        peaks0 = sorted(itertools.chain(*(cc.peaks() for cc in ccinfo)),
                        key=lambda cc: cc.crossCorr, reverse=True)

        peaks = []
        for peak in peaks0:
            # exclude +- 600 km/s (0.002c) in next searches
            z0, z1 = ((1 + peak.redshift)
                      * (1 + np.array([-0.002, 0.002]))) - 1.
            for p in peaks:
                if (z0 < p.redshift) and (p.redshift < z1):
                    break
            else:
                peaks.append(peak)
                yield peak

    def CalculateRedshiftErr(self, peaks):
        """Calculate the redshift uncertainty for zBest
        (Added 17 Apr, 2023 by Young-Lo based on Eqs. 10 & 11 in Baldry+2014.)

        Parameters
        ----------

        peaks : :class:`.QXPSpectrum`

        Returns
        -------

        float: peakZerr
        """
        from astropy import constants as const

        peakCC = peaks[0].crossCorr
        peakZ = peaks[0].redshift
        peakInfo = peaks[0].ccinfo
        peakInd = peaks[0].index

        # within 600 km/s range.
        pZ600lo, pZ600up = ((1 + peakZ) * (1 + np.array([-0.002, 0.002]))) - 1

        # Index for within 600 km/s range > always 44? Can we fix?
        for i600lo in range(0, 100):
            index_low_lim_check = peakInd - i600lo
            if index_low_lim_check == 0:
                break
            if peaks[0].ccinfo.redshift[peakInd - i600lo] < pZ600lo:
                break

        for i600up in range(0, 100):
            index_high_lim_check = peakInd + i600up
            if index_high_lim_check == len(peaks[0].ccinfo.redshift):
                break
            if peaks[0].ccinfo.redshift[peakInd + i600up] > pZ600up:
                break

        ccWithin600 = peakInfo.crossCorr[peakInd - i600lo + 1:peakInd + i600up]
        z_err_index = np.where(ccWithin600 > peakCC / 2.)

        # 13.8 km/s from GAMA survey in Baldry+2014
        v_fwhm = (13.8 * len(ccWithin600[z_err_index]))

        Vel = v_fwhm / (1 + peakCC)
        c0 = 19.12  # taken from Baldry+2014
        c1 = 0.525
        cInKmS = const.c.to('km/s').value  # speed of light in km/s.

        peakZerr = np.sqrt(c0**2 + (Vel * c1)**2) * ((1 + peakZ) / cInKmS)

        return peakZerr
