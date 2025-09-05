import numpy as np
import astropy.units as u

from ..spectrum import QXPSpectrum
from .. import preprocess


def test_setminimumerror():
    wl = np.arange(3000., 9000.) * u.Angstrom
    flux = np.ones(wl.shape) * u.one        # this needs to be a quantity
    flux_error = np.ones(wl.shape) * u.one  # this needs to be a quantity
    flux_error[1000] = 0.0
    spectrum = QXPSpectrum(wl, flux, flux_error)
    process = preprocess.SetMinimumError()
    res = process(spectrum)
    assert res.flux_error[1000] == 0.7
    assert np.array_equal(res.wavelength, spectrum.wavelength)
    assert np.array_equal(res.flux, spectrum.flux)
    assert np.array_equal(res.flux_error[:1000], spectrum.flux_error[:1000])
    assert np.array_equal(res.flux_error[1001:], spectrum.flux_error[1001:])


def test_normalizebyerror():
    wl = np.arange(3000., 9000.) * u.Angstrom
    flux = np.random.random(wl.shape) * u.one
    flux_error = np.random.random(wl.shape) * u.one
    spectrum = QXPSpectrum(wl, flux, flux_error)
    process = preprocess.NormalizeByError()
    res = process(spectrum)
    assert np.array_equal(res.wavelength, spectrum.wavelength)
    np.testing.assert_allclose(res.flux * spectrum.flux_error**2,
                               spectrum.flux)


def test_subtractpolynomial():
    wl = np.arange(3000., 9000.) * u.Angstrom
    # random() gives a random between 0 and 1, which corresponds to a
    # "polynomial" (constant) of 0.5
    flux = np.random.random(wl.shape) * u.one
    spectrum = QXPSpectrum(wl, flux)
    process = preprocess.SubtractPolynomial()
    res = process(spectrum)
    assert np.array_equal(res.wavelength, spectrum.wavelength)
    np.testing.assert_allclose(spectrum.flux - res.flux, 0.5, atol=0.05)


def test_smoothing():
    wl = np.arange(3000., 9000.) * u.Angstrom
    flux = np.zeros(wl.shape) * u.one        # this needs to be a quantity
    flux[1000] = 1.0
    spectrum = QXPSpectrum(wl, flux)
    process = preprocess.SmoothingFilter(None)
    res = process(spectrum)
    np.testing.assert_equal(res.wavelength, spectrum.wavelength)
    np.testing.assert_allclose(res.flux[:930], spectrum.flux[:930])
    np.testing.assert_allclose(res.flux[1071:], spectrum.flux[1071:])
    np.testing.assert_allclose((spectrum.flux[950:1050] - res.flux[950:1050]),
                               0.008264, atol=1e-6)
