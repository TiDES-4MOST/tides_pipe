import numpy as np
import astropy.units as u


def air_to_vac(wavelength):
    """Internal AutoZ routines for converting air to vacuum wavelengths

    Function interpolate air wavelengths to vacuum wavelengths using a
    lookup table. Equation comes from the SDSS web page:
    http://www.sdss.org/dr7/products/spectra/vacwavelength.html.
    Reference to :adsabs:`1991ApJS...77..119M`.

    Parameters
    ----------
    wavelength : :class:`astropy.units.Quantity`
        Wavelength in air

    Returns
    -------
     :class:`astropy.units.Quantity`
        Wavelength in vacuum


    Examples
    --------

    Return the vacuum wavelength of 5000 Å::

        >>> air_to_vac(5000*u.Angstrom)
        <Quantity 5001.39606749 Angstrom>

    """
    vacLookup = np.arange(3500, 11500, 0.1)
    convertLookup = (1.0 + 2.735182e-4 + 131.4182 / vacLookup**2
                     + 2.76249e8 / vacLookup**4)

    airLookup = vacLookup / convertLookup
    convert = np.interp(wavelength.to_value("Angstrom"), airLookup,
                        convertLookup)
    return wavelength * convert


def vac_to_air(wavelength):
    """Converting air wavelengths to vacuum wavelengths

    Function interpolate vacuum wavelengths to air wavelengths using a
    lookup table. Equation comes from the SDSS web page:
    http://www.sdss.org/dr7/products/spectra/vacwavelength.html.
    Reference to Morton (1991, ApJS, 77, 119).

    Parameters
    ----------
    wavelength : :class:`astropy.units.Quantity`
        Wavelength in vacuum

    Returns
    -------
     :class:`astropy.units.Quantity`
        Wavelength in air

    Examples
    --------

    Return the air wavelength of 5000 Å::

        >>> vac_to_air(5000*u.Angstrom)
        <Quantity 4998.60430507 Angstrom>

    """
    v = wavelength.to_value(u.Angstrom)
    return wavelength / (1.0 + 2.735182E-4
                         + 131.4182 / v**2
                         + 2.76249E8 / v**4)
