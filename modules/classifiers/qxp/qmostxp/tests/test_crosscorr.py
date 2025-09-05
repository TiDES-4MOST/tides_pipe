import numpy as np
import pytest

import astropy.units as u
from astropy.constants import c as light_speed

from .. import CrossCorrInfo, QXPSpectrum, QXPTemplate


@pytest.mark.parametrize('redshift', [0., 0.05, 0.1, 0.5, 1.0, 1.4])
@pytest.mark.parametrize('template_id',
                         [23, 24, 25, 26, 27, 28, 40, 41,
                          42, 43, 44, 45, 46, 47, 48])
def test_crosscorrinfo(template_id, redshift):
    """Test the correllation of a template with a shifted self

    For some reason, this doesn't work with all templates, and not for
    all wavelength ranges.

    """
    gap = 0.0001 / 5 * u.dex(u.one)
    log_wl = np.arange(3., 4., gap.value) * u.dex(u.Angstrom)
    template = QXPTemplate.read(log_wl, templateNumbers=[template_id])[0]
    shift = np.log(redshift + 1) / np.log(gap.physical)
    spectrum = QXPSpectrum(template.wavelength + gap * shift,
                           template.flux * u.one)
    spectrum = spectrum.subspectrum(3600 * u.Angstrom, 9500 * u.Angstrom)
    spectrum = spectrum.rebin(template.wavelength)
    cc = CrossCorrInfo(spectrum, template, gap)
    peaks = sorted(cc.peaks(), key=lambda p: p.crossCorr, reverse=True)
    np.testing.assert_allclose(peaks[0].redshift, redshift, atol=1e-3)


@pytest.mark.parametrize('heliovel', [
    -15 * u.km / u.s,
    0 * u.m / u.s,
    15000 * u.m / u.s,
])
def test_crosscorrinfo_heliovel(heliovel):
    """Check that the heliocentric velocity is propagated correctly

    """
    gap = 0.0001 / 5 * u.dex(u.one)
    log_wl = np.arange(3., 4., gap.value) * u.dex(u.Angstrom)
    template = QXPTemplate.read(log_wl, templateNumbers=[42])[0]
    spectrum = QXPSpectrum(template.wavelength, template.flux * u.one)
    cc = CrossCorrInfo(spectrum, template, gap, helioVel=heliovel)
    peaks = sorted(cc.peaks(), key=lambda p: p.crossCorr, reverse=True)
    np.testing.assert_allclose(peaks[0].redshift,
                               heliovel / light_speed, atol=1e-3)
