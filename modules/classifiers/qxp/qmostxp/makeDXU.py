from astropy.io.fits import PrimaryHDU, BinTableHDU, HDUList, Header
from astropy.table import QTable, Column
import astropy.units as u
from .version import version as qxp_version

primaryHeader = """
PRODCATG= 'SCIENCE.CATALOG'    / Data product category
ORIGIN  = 'ESO-PARANAL'        / Observatory or facility
TELESCOP= 'VISTA   '           / ESO telescope designation
INSTRUME= '4MOST   '           / Instrument name
OBJECT  = 'null    '           / Target designation as given by PI
MJD-OBS = ''                   / Start of (earliest) observation
MJD-END = ''                   / End of (latest) observation
PROG_ID = ''                   / Observing run identification code: TP.C-NNNN(R)
OBSTECH = ''                   / Technique used during observation
PROCSOFT= ''      / Reduction software system and version number
REFERENC= 'Someone et al.'     / Primary publication describing data product
PROVXTN =                    2 / Provenance of files originating data product
WAVELMIN=                370.0 / Min wavelength for scientific information [nm]
WAVELMAX=                950.0 / Max wavelength for scientific information [nm]
CHECKSUM= '' / HDU checksum updated XXXX-XX-XXTYY:YY:YY
DATASUM = '' / Data unit checksum updated XXXX-XX-XXTYY:YY:YY
QMNODE  = 'QXP     '           / 4MOST pipeline that produced this data product
RELEASE =              59946.0 / Data release of this data product in MJD
DXUDOC  = 'MST-TNO-PSC-20308-9238-0001' / Data eXchange Unit document
DATETIME= '' / DateTime stamp when file submitted to NXP
PUSER   = 'IWG8 developers'    / User responsible for running pipeline
PCONTACT= 'iwg-exgalpipe@4most.eu' / Email regarding this product
"""  # noqa: E501


class FileInfo:
    """Collect file information for header from the input files

    """
    def __init__(self):
        self.prop = dict()
        self.files = list()

    def add(self, header, fpath):
        header_keywords = [
            'MJD-OBS', 'MJD-END', 'PROG_ID', 'OBSTECH', 'OBID1'
        ]
        for key in header_keywords:
            if key in header:
                self.prop.setdefault(key, set()).add(header[key])

        self.files.append(fpath.name)

    def makePrimaryHDU(self):
        """Create primary HDU

        The created header is documented in :ref:`primary-header`.

        """
        header = Header.fromstring(primaryHeader, sep='\n')

        if 'MJD-OBS' in self.prop:
            header['MJD-OBS'] = (
                min(self.prop['MJD-OBS']),
                'Start of (earliest) observation'
            )

        if 'MJD-END' in self.prop:
            header['MJD-END'] = (
                max(self.prop['MJD-END']),
                'End of (latest) observation'
            )

        if 'PROG_ID' in self.prop:
            if len(self.prop['PROG_ID']) == 1:
                header['PROG_ID'] = (
                    min(self.prop['PROG_ID']),
                    'Observing run identification code'
                )
            else:
                header['PROG_ID'] = (
                    'MULTI',
                    'Observing run identification code'
                )
                for i, progid in enumerate(self.prop['PROG_ID']):
                    header[f'PROGID{i+1}'] = (
                        progid,
                        'Run identification code #{i+1}'
                    )

        if 'OBSTECH' in self.prop:
            header['OBSTECH'] = (
                min(self.prop['OBSTECH']),
                'Technique used during observation'
            )

        if 'OBID1' in self.prop:
            for i, obid in enumerate(self.prop['OBID1']):
                header[f'OBID{i+1}'] = (
                    obid,
                    f'The Observation block ID #{i+1}'
                )

        header['PROCSOFT'] = (
            f'qxp/{qxp_version}',
            'Reduction software system and version number'
        )

        return PrimaryHDU(header=header)

    def makeProvenance(self):
        """Create a table with provenance information

        This is a simple table with just one column ``PROV`` with one
        input file name per row, see also :ref:`provenance-table`.


        Returns
        -------

        :class:`astropy.table.QTable`
            The created provenance table.


        """  # noqa: E501
        return QTable({'PROV': self.files})


catalogTableColumns = {
    'TARGET_UID': {
        'dtype': 'i8',
        'description': 'MOST object name from coordinates',
        'ucd': 'meta.id',
    },
    'TARGET_CNAME': {
        'dtype': 'a32',
        'description': '4MOST object CNAME from coordinates',
        'ucd': 'meta.id',
    },
    'TARGET_RA': {
        'dtype': 'f8',
        'description': 'Catalogue RA of object',
        'unit': 'deg',
        'range': (0., 360.),
        'format': '9.2f',
        'ucd': 'pos.eq.ra',
    },
    'TARGET_DEC': {
        'dtype': 'f8',
        'description': 'Catalogue Declination of object in decimal degrees',
        'unit': 'deg',
        'range': (-90., 90.),
        'format': '9.2f',
        'ucd': 'pos.eq.dec',
    },
    'zBest': {
        'dtype': 'f8',
        'description': 'Best fit heliocentric redshift',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift',
    },
    'zBestErr': {
        'dtype': 'f8',
        'description': 'Best fit heliocentric redshift error',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift;stat;error',
    },
    'zBestCCSig': {
        'dtype': 'f8',
        'description': 'Best fit heliocentric redshift cross-correlation'
                       ' peak value divided by RMS CC value',
        'range': (0., 100.),
        'format': '9.2f',
        'ucd': 'stat.correlation',
    },
    'zBestFOM': {
        'dtype': 'f8',
        'description': 'Figure of merit of best fit redshift',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'stat.fit.goodness',
    },
    'zBestProb': {
        'dtype': 'f8',
        'description': 'Estimated probability of redshift success',
        'range': (0., 1.),
        'format': '9.2f',
        'ucd': 'stat.fit.goodness',
    },
    'zBestT_ID': {
        'dtype': 'i2',
        'description': 'Best fit heliocentric redshift template identifier',
        'range': (0, 1000),
        'ucd': 'meta.id',
    },
    'zBestType': {
        'dtype': 'a32',
        'description': 'Best fit heliocentric redshift template type',
        'ucd': 'meta.id',
    },
    'z2': {
        'dtype': 'f8',
        'description': '2nd best fit heliocentric redshift',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift',
    },
    'z2Err': {
        'dtype': 'f8',
        'description': '2nd best fit heliocentric redshift error',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift;stat;error',
    },
    'z2CCSig': {
        'dtype': 'f8',
        'description': '2nd best fit heliocentric redshift cross-correlation'
                       ' peak value divided by RMS CC value',
        'range': (0., 100.),
        'format': '9.2f',
        'ucd': 'stat.correlation',
    },
    'z2T_ID': {
        'dtype': 'i2',
        'description': '2nd best fit heliocentric redshift template'
                       ' identifier',
        'range': (0, 1000),
        'ucd': 'meta.id',
    },
    'z2Type': {
        'dtype': 'a32',
        'description': '2nd best fit heliocentric redshift template type',
        'ucd': 'meta.id',
    },
    'z3': {
        'dtype': 'f8',
        'description': '3rd best fit heliocentric redshift',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift',
    },
    'z3Err': {
        'dtype': 'f8',
        'description': '3rd best fit heliocentric redshift error',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift;stat;error',
    },
    'z3CCSig': {
        'dtype': 'f8',
        'description': '3rd best fit heliocentric redshift cross-correlation'
                       ' peak value divided by RMS CC value',
        'range': (0., 100.),
        'format': '9.2f',
        'ucd': 'stat.correlation',
    },
    'z3T_ID': {
        'dtype': 'i2',
        'description': '3rd best fit heliocentric redshift template'
                       ' identifier',
        'range': (0, 1000),
        'ucd': 'meta.id',
    },
    'z3Type': {
        'dtype': 'a32',
        'description': '3rd best fit heliocentric redshift template type',
        'ucd': 'meta.id',
    },
    'z4': {
        'dtype': 'f8',
        'description': '4th best fit heliocentric redshift',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift',
    },
    'z4Err': {
        'dtype': 'f8',
        'description': '4th best fit heliocentric redshift error',
        'range': (0., 10.),
        'format': '9.2f',
        'ucd': 'src.redshift;stat;error',
    },
    'z4CCSig': {
        'dtype': 'f8',
        'description': '4th best fit heliocentric redshift cross-correlation'
                       ' peak value divided by RMS CC value',
        'range': (0., 100.),
        'format': '9.2f',
        'ucd': 'stat.correlation',
    },
    'z4T_ID': {
        'dtype': 'i2',
        'description': '4th best fit heliocentric redshift template'
                       ' identifier',
        'range': (0, 1000),
        'ucd': 'meta.id',
    },
    'z4Type': {
        'dtype': 'a32',
        'description': '4th best fit heliocentric redshift template type',
        'ucd': 'meta.id',
    },
}


def makeCatalogTable(length):
    """Create an empty catalog table of a given length

    The columns of the table are specified in :ref:`catalogue-table`.

    Parameters
    ----------

    length : :class:`int`
        Desired length of the table

    Returns
    -------
    :class:`astropy.table.QTable`
        The created (empty) table

    Examples
    --------

    Create an empty table with 4 rows::

        >>> tbl = makeCatalogTable(4)
        >>> print(tbl)
        TARGET_UID TARGET_CNAME TARGET_RA TARGET_DEC ... z4Type
                                   deg       deg     ...
        ---------- ------------ --------- ---------- ... ------
                 0                   0.00       0.00 ...
                 0                   0.00       0.00 ...
                 0                   0.00       0.00 ...
                 0                   0.00       0.00 ...
    """
    return QTable([
        Column(name=name, length=length,
               description=col.get('desription'),
               dtype=col.get('dtype'),
               unit=u.Unit(col.get('unit')) if 'unit' in col else None,
               format=col.get('format'),
               meta={
                   'TUCD': col.get('ucd'),
                   'TDMIN': col.get('range')[0] if 'range' in col else None,
                   'TDMAX': col.get('range')[1] if 'range' in col else None,
               })
        for name, col in catalogTableColumns.items()
    ])


def makeDXU(primary, catalog, provenance):

    """Combine the primary header and the catalog into a FITS HDU list

    Parameters
    ----------

    primary : :class:`astropy.io.fits.PrimaryHDU`
        Primary header to use, usually created with :func:`.makePrimaryHDU`

    catalog : :class:`astropy.table.QTable`
        Catalog table, usually created with :func:`.makeCatalogTable` and
        then filled.

    provenance : :class:`astropy.table.QTable`
        Provenance table, usually created with :func:`.makeProvenance`.

    Returns
    -------
    :class:`astropy.io.fits.HDUList`
        The output FITS HDU list

    """
    catalogHDU = BinTableHDU(catalog, name='PHASE3CATALOGUE')

    # Fix non-standard column metadata header
    for i, col in enumerate(catalog.columns.values()):
        if not hasattr(col, 'meta'):
            continue
        for name in ['TUCD', 'TDMIN', 'TDMAX']:
            value = col.meta.get(name)
            if value is not None:
                catalogHDU.header[f'{name}{i+1}'] = value

    provenanceHDU = BinTableHDU(provenance, name='PHASE3PROVENANCE')

    return HDUList([primary, catalogHDU, provenanceHDU])
