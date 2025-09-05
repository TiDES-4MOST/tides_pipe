import numpy as np
from astropy.constants import c as light_speed
import astropy.units as u


class CrossCorrInfo:
    """Provide cross correllation information between a spectrum and a template

    This class computes the cross correllation, and holds the
    information of all cross correllation peaks.

    Parameters
    ----------

    spec : :class:`.QXPSpectrum`
        Preprocessed input spectrum with logarithmically spaced
        wavelength.

    template : :class:`QXPTemplate`
        Template spectrum, using the same wavelength binning as the
        input spectrum.

    gap : :class:`float`
        Redshift step between bins in spec and template

    helioVel : :class:`astropy.units.Quantity`
        helocentric correction value [km/s]

    Attributes
    ----------

    template :  :class:`QXPTemplate`
        Template spectrum

    redshift : :class:`numpy.ndarray`
        Zero-centered float array of redshift values

    crossCorr : :class:`numpy.ndarray`
        Normalized float array corresponding cross correlation values

    posPeak :   :class:`numpy.ndarray`
        Boolean array identifying the local maxima of the cross correlation.

    negPeak :   :class:`numpy.ndarray`
        Boolean array identifying the local minima of the cross correlation.

    """
    def __init__(self, spec, template, gap, helioVel=0 * u.km / u.s):
        self.template = template
        self.gap = gap
        self.setSpectrum(spec, helioVel)

    def setSpectrum(self, spec, helioVel):
        crossCorr = self.template.crossCorr(spec)

        # calculate redshifts
        redshift = self.gap.physical ** np.arange(-(len(crossCorr) // 2),
                                                  (len(crossCorr) + 1) // 2)
        redshift *= 1 + self.template.redshift
        redshift *= 1 + (helioVel / light_speed).to_value('')
        redshift -= 1.0

        # Limit the range to the one specified by the template
        allowedZ = slice(
            np.searchsorted(redshift, self.template.allowedZRange[0], "left"),
            np.searchsorted(redshift, self.template.allowedZRange[1], "right"))
        self.crossCorr = crossCorr[allowedZ].value
        self.redshift = redshift[allowedZ]

        rmsZ = slice(
            np.searchsorted(self.redshift, self.template.rmsZRange[0], "left"),
            np.searchsorted(self.redshift, self.template.rmsZRange[1], "right")
        )
        self.crossCorr = self.crossCorr[rmsZ]
        self.redshift = self.redshift[rmsZ]

        # Subtract trimmed mean excluding top and bottom 4% of points.
        # This brings more symmetry to positive and negative peaks.
        self.crossCorr -= mean_reject(self.crossCorr, 0.04)

        # Find local extrema in the cross correlation values
        self.posPeak = np.zeros(len(self.crossCorr), dtype=bool)
        self.posPeak[1:-1] = ((self.crossCorr[1:-1] >= self.crossCorr[:-2]) &
                              (self.crossCorr[1:-1] > self.crossCorr[2:]))
        self.negPeak = np.zeros(len(self.crossCorr), dtype=bool)
        self.negPeak[1:-1] = ((self.crossCorr[1:-1] <= self.crossCorr[:-2]) &
                              (self.crossCorr[1:-1] < self.crossCorr[2:]))

        # normalisation using turning points - divide by root mean square.
        self.crossCorr /= np.sqrt(np.mean(
            self.crossCorr[self.posPeak | self.negPeak]**2))

        # TODO this is commented out in Ivans code, not sure to include
        # normalization using values of positive peaks and
        # negative values of negative peaks.
        # [JK] commented out:
        # testVals = np.concatenate((self.crossCorr[self.posPeak],
        #                            -self.crossCorr[self.negPeak]))
        # trimmedMean = mean_reject(testVals, 0.04)
        # sdEstimate = np.sqrt(np.mean((testVals - trimmedMean)**2))
        # self.crossCorr -= trimmedMean
        # self.crossCorr /= sdEstimate

    def peaks(self):
        """Iterate over all peaks (local maxima).

        This can be used to subsequently go over all local maxima,
        ordered by redshift.

        Returns
        -------
        iterator of :class:`.CrossCorrPeak`
            Iterator over the peaks

        """
        # cross correlation values of peaks in allowed range
        return (CrossCorrPeak(self, index)
                for index in np.where(self.posPeak)[0])


class CrossCorrPeak:
    """Cross correlation peak

    This class conveniently combines all properties of a peak in a
    cross correlation. This is returned by
    :meth:`.CrossCorrInfo.peaks`.

    Parameters
    ----------

    ccinfo : :class:`.CrossCorrInfo`
        Parent cross corellation class

    index : :class:`int`
        Index of the peak in the class

    """
    def __init__(self, ccinfo, index):
        self.ccinfo = ccinfo
        self.index = index

    @property
    def template(self):
        """Template used for cross correllation; :class:`QXPTemplate`"""
        return self.ccinfo.template

    @property
    def redshift(self):
        """Adjusted redshift value of the peak; :class:`float`

        The adjustment is done with a quadratic fit around the peak.
        If the fit fails, the unadjusted redhift is returned.

        """
        n = 2  # number of elements around peak value to fit
        xmin = max(self.index - n, 0)
        xmax = min(self.index + n + 1, len(self.ccinfo.redshift))

        redshift = self.ccinfo.redshift[xmin:xmax]
        crossCorr = self.ccinfo.crossCorr[xmin:xmax]
        r0, r1, r2 = np.polynomial.Polynomial.fit(redshift, crossCorr,
                                                  deg=2, domain=[])
        return -0.5 * r1 / r2 if r2 != 0 else self.ccinfo.redshift[self.index]

    @property
    def crossCorr(self):
        """Cross correlation value at the peak (unadjusted); :class:`float`"""
        return self.ccinfo.crossCorr[self.index]


def CalculateCertainty(rmsNorm, meanadNorm, ccSigma):
    """Calculate certainty of cross-correlation

    Calculate the probability of cross-correlation being correct

    Justfication for this comes from :adsabs:`2014MNRAS.441.2440B`.

    Revision by Y.-L.Kim (Dec2022) (Will be removed after finalizing the code.)

    Parameters
    ----------

    rmsNorm : :class:`float`
        Root mean square of the spectral flux

    meanadNorm : :class:`float`
        Mean absolute deviation of the spectral flux

    ccSigma : array[4] of :class:`float`
        Sigma of the first four peaks >> this is a peak value, why ccSigma?

    Returns
    -------
    pair of floats
        Certainity of the first value being correct, and Figures Of Merit

    Examples
    --------

    Calculate the probability for a sample list::

        >>> ccSigma = [8.78, 4.77, 4.71, 4.53]
        >>> CalculateCertainty(1.74, 1.19, ccSigma)
        (0.3214976886623978, 3.438585864014168)

    """
    # Calculate root mean square to mean absolute deviation ratio.
    rmsMadRatio = float(rmsNorm / meanadNorm)

    # Based on Baldry+2014 eq. (2).
    # Calculate ratio of first peak compared to RMS of 2nd/3rd/4th.
    ccRatio = (ccSigma[0] / np.sqrt(np.square(ccSigma[1:4]).sum()))
    # CHANGED 'mean' to 'sum' by Kim

    # The following calibrations were determined for the GAMA survey.
    # It is unclear how they will perform with different surveys.

    # Based on Baldry+2014 eq. (3).
    # Define FOM, lower of cc_sigma and a converted cc_sigma1to234.
    # !! Intercept & slope (0.4&2.8 from AAOmega spectra) will be updated.
    ccFOMpre = min(ccSigma[0], np.polynomial.Polynomial((0.4, 2.8))(ccRatio))

    # Basedon Baldry+2014 eq.(4).
    # Adjustment to FOM for large rms_mad_ratio.
    rmrAdjustment = min(2.1, max(0, 1.5 * (rmsMadRatio - 1.8)))
    ccFOM = max(2.6, ccFOMpre - rmrAdjustment)

    # Set redshift confidence from CC_FOM.
    # A bit different from Baldry+2014 eq.(8). e.g., b0=3.68, b1=0.63
    x = (ccFOM - 3.70) / 0.70
    prob = (np.tanh(x) + 1) / 2
    return prob, ccFOM


def mean_reject(data, reject=0):
    """Mean of a set of values after rejecting lowest and highest values

    Take the mean of a set of values after rejecting the lowest and
    highest values. This is called the 'trimmed mean' or 'truncated
    mean'.

    Parameters
    ----------

    data :
        1D data values

    reject :
        fraction of values to reject

    """
    try:
        m0, m1 = np.nanquantile(data, (reject, 1 - reject))
    except TypeError:
        print(f"{data=}")
    return np.nanmean(data[(m0 < data) & (data < m1)])
