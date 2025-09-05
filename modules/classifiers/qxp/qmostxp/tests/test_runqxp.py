import pytest

import numpy as np

from .. import test_data, runQXP
from .. import __version__ as qxpversion


@pytest.fixture(scope='module')
def catalog():
    return runQXP([test_data])


@pytest.mark.parametrize('key,val', [
    ('PRODCATG', 'SCIENCE.CATALOG'),
    ('ORIGIN', 'ESO-PARANAL'),
    ('TELESCOP', 'VISTA'),
    ('MJD-OBS', 59105.875),
    ('MJD-END', 59105.88888889),
    ('PROG_ID', '60.A-9296(A)'),
    ('OBSTECH', 'MOS'),
    ('WAVELMIN', 370.),
    ('WAVELMAX', 950.),
    ('PROCSOFT', f'qxp/{qxpversion}'),
    ('QMNODE', 'QXP'),
    ('DXUDOC', 'MST-TNO-PSC-20308-9238-0001'),
    ('PUSER', 'IWG8 developers'),
    ('PCONTACT', 'iwg-exgalpipe@4most.eu'),
])
def test_catalog_primary_header(catalog, key, val):
    """Test the example catalog primary header for DXU conformance

    See MST-TNO-PSC-20308-9238-0001, section 6.5.5.1 amd 6.6.1.2
    """
    assert catalog[0].header[key] == val


@pytest.mark.parametrize('key,val', [
    ('XTENSION', 'BINTABLE'),
    ('BITPIX', 8),
    ('NAXIS', 2),
    ('NAXIS1', 304),
    ('NAXIS2', 2),
    ('EXTNAME', 'PHASE3CATALOGUE'),
    ('TFIELDS', 26),
])
def test_catalog_extension_header(catalog, key, val):
    """Test the example catalog table header for DXU conformance

    See MST-TNO-PSC-20308-9238-0001, section 6.6.1.3

    This is incomplete in the sense that it only checks the keywords
    that are not column specific.

    """
    assert catalog[1].header[key] == val


@pytest.mark.parametrize('key,val', [
    ('XTENSION', 'BINTABLE'),
    ('BITPIX', 8),
    ('NAXIS', 2),
    ('NAXIS1', 47),
    ('NAXIS2', 2),
    ('EXTNAME', 'PHASE3PROVENANCE'),
    ('TFIELDS', 1),
    ('TTYPE1', 'PROV'),
    ('TFORM1', '47A'),
])
def test_catalog_provenance_header(catalog, key, val):
    """Test the example provenance table header for DXU conformance

    See MST-TNO-PSC-20308-9238-0001, section 6.6.1.5

    This is incomplete in the sense that it only checks the keywords
    that are not column specific.

    """
    assert catalog[2].header[key] == val


def test_catalog_provenance_table(catalog):
    """Check the content of the provenance catalog

    """
    np.testing.assert_array_equal(
        catalog[2].data['PROV'],
        ['qmost_00033103-2820362_20200913_100030_LJ1.fits',
         'qmost_00033300-2847587_20200913_100025_LJ1.fits']
    )
