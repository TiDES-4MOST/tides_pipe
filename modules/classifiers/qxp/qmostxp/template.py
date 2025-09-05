import pathlib

import numpy as np
import astropy.units as u
from astropy.table import QTable

from .spectrum import QXPSpectrum

datadir = pathlib.Path(__file__).parent / 'data'


class QXPTemplate(QXPSpectrum):
    """Template to be used in the QXP pipeline

    This template features a logarithmic wavelength scale which is
    usually shaed with all templates.

    The usual way to create a list of templates is to
    :meth:`QXPTemplate.read` static method.

    Parameters
    ----------

    templateId : :class:`int`
        Unique template id; a small integer

    name : :class:`str`
        Template file name

    wavelength : :class:`astropy.units.Quantity`
        Wavelength array [Å]

    flux : array-like
        Flux array

    redshift : :class:`float`
        Redshift for the template

    rmsZRange : :class:`tuple`
        Pair (min, max) of the redshift range to be used for RMS calculation

    allowedZRange : :class:`tuple`
        Pair (min, max) of the allowed redshift range

    ttype : :class:`str`
        Template type. Usually one of "Stellar", "late-type stellar",
        "Passive Galaxy", "Star-forming Galaxy", "QSO", "other".

    """

    def __init__(self, templateId, name, wavelength, flux, redshift,
                 rmsZRange, allowedZRange, ttype):
        self.templateId = templateId
        self.name = name
        super(self.__class__, self).__init__(wavelength, flux)
        self.redshift = redshift
        self.rmsZRange = rmsZRange
        self.allowedZRange = allowedZRange
        self.ttype = ttype

    template_file = datadir / 'filtered-templates.fits'
    """Default template file"""

    @staticmethod
    def read(wavelength, templateNumbers=np.r_[2:14, 16:22, 40:47],
             fname=None):
        """Read and rebin template data to a common logarithmic wavelength

        Parameters
        ----------

        wavelength : :class:`astropy.units.Quantity`
            common (logarithmic) wavelength scale [dex(Å)]

        templateNumbers : :class:`list`
            List of template numbers to use. Default: 20 stellar and 8
            galaxy templates

        fname : :class:`pathlib.Path` or :class:`str`
            Templates fname to use. If not given, the built-in template
            is used.


        Returns
        -------

        list of :class:`.QXPTemplate`
            List of spectral templates, each one rebinned to the given
            wavelength scale. Each template has the columns ``lambda``
            [dex(Å)] and ``flux``. Additionally, each table has the
            following metadata:

                * ``redshift`` - redshift for the template spectrum
                * ``templateNum`` Template number/id (from the ``TEMPLATE_NUM``
                  column of the templates file)
                * ``specName`` - original file name (from the ``FILE`` column
                  of the templates table)
                * ``numPoints`` original number of points (from the
                  ``NUM_POINTS`` column of the templates file)

        Examples
        --------

        Read a few templates using a limited wavelength range::

            >>> LamScale = (np.arange(5) * 0.00002 + 3.62) * u.dex(u.Angstrom)
            >>> tpls = QXPTemplate.read(LamScale, np.r_[2:4])
            >>> print(tpls[1].flux)
            [-0.01350931 -0.01873289 -0.02395648 -0.02918007 -0.03440366]

        """
        if fname is None:
            fname = QXPTemplate.template_file
        tdata = QTable.read(fname)
        templates = []
        for j in templateNumbers:
            if j not in tdata['TEMPLATE_NUM']:
                continue
            row = tdata[tdata['TEMPLATE_NUM'] == j][0]
            name = row['FILE'].strip()
            redshift = row['REDSHIFT']
            flux = row['SPEC'][row['LOG_LAMBDA'] != 0]
            wl = row['LOG_LAMBDA'][row['LOG_LAMBDA'] != 0] * u.dex(u.Angstrom)
            tflux = np.interp(wavelength, wl, flux)
            rmsZRange, allowedZRange, ttype = QXPTemplate._templateRanges(j)
            templates.append(QXPTemplate(j, name, wavelength, tflux, redshift,
                                         rmsZRange, allowedZRange, ttype))

        return templates

    @staticmethod
    def _templateRanges(templateId):
        """Return ranges and types of QXP templates

        Parameters
        ----------

        templateId : :class:`int`
            Template number

        Returns
        -------
        :class:`tuple`
            Triple with rms ZRange, allowedZRange, template type

        Examples
        --------

        Return the info of the template number one

            >>> QXPTemplate._templateRanges(1)
            ([-0.1, 0.5], [-0.002, 0.002], 'Stellar')

        """

        rmsZRange = [-0.1, 0.5]
        allowedZRange = [-0.002, 0.002]
        tname = 'other'
        if templateId in [11, 12, 13, 14, 15, 17, 19, 22]:
            # late-type stellar templates - power is at red end
            rmsZRange = [-0.2, 0.4]
            allowedZRange = [-0.002, 0.002]
            tname = 'late-type_stellar'
        elif templateId <= 22:   # remaining stellar templates
            rmsZRange = [-0.1, 0.5]
            allowedZRange = [-0.002, 0.002]
            tname = 'Stellar'
        elif templateId >= 23 and templateId <= 28:  # orig galaxy templates
            rmsZRange = [-0.1, 0.8]
            rmsZRange = [-0.1, 1.5]
            allowedZRange = [-0.005, 1.500]
            if templateId == 23:
                tname = 'Passive_galaxy'
            if templateId == 24:
                tname = 'Star-forming_galaxy'
            if templateId == 25:
                tname = 'Star-forming_galaxy'
            if templateId == 26:
                tname = 'Star-forming_galaxy'
            if templateId == 27:
                tname = 'Star-forming_galaxy'
            if templateId == 28:
                tname = 'Passive_galaxy'
        elif templateId == 29 or templateId == 32:
            # QSO templates - not working reliably with GAMA - needs highz set
            rmsZRange = [-0.1, 5]
            allowedZRange = [0, 5.500]
            tname = 'QSO'
        elif templateId == 30 or templateId == 31:
            # QSO templates - not working reliably with GAMA - needs highz set
            rmsZRange = [-0.1, 5]
            allowedZRange = [1.5, 5.500]
            tname = 'QSO'
        elif templateId >= 33 and templateId <= 49:  # other galaxy templates
            rmsZRange = [-0.1, 0.9]
            rmsZRange = [-0.1, 1.5]
            allowedZRange = [-0.005, 1.500]
            tname = 'Star-forming_galaxy'
            if templateId == 40:
                tname = 'Passive_galaxy'
            if templateId == 41:
                tname = 'Passive_galaxy'
        elif templateId >= 50 and templateId < 60:
            rmsZRange = [-0.1, 2.0]
            allowedZRange = [-0.005, 2.000]
        elif templateId >= 60 and templateId <= 72:
            rmsZRange = [-0.1, 5.]
            allowedZRange = [-0.005, 2.000]
        elif templateId > 73 and templateId <= 80:
            rmsZRange = [-0.1, 10.0]
            allowedZRange = [-0.005, 2.000]
        elif templateId > 80:
            rmsZRange = [-0.1, 2.0]
            allowedZRange = [-0.005, 2.000]
            # TODO this should be = z_prior, look up in do_crossvorr.pro in IDL

        if templateId == 64:
            allowedZRange = [2.0, 6.5]
        if templateId == 65:
            allowedZRange = [2.0, 6.5]
        if templateId == 66:
            allowedZRange = [2.0, 6.5]
        if templateId == 67:
            allowedZRange = [2.0, 6.5]

        if templateId == 76:
            allowedZRange = [2.0, 6.5]
        if templateId == 77:
            allowedZRange = [2.0, 6.5]
        if templateId == 78:
            allowedZRange = [2.0, 6.5]
        if templateId == 79:
            allowedZRange = [2.0, 6.5]
        if templateId == 80:
            allowedZRange = [2.0, 6.5]

        return (rmsZRange, allowedZRange, tname)

    @staticmethod
    def info(templateNumbers):
        """Return QXP template information table

        Parameters
        ----------
        nums : :class:`int` or iterable
            Template numbers to print


        Returns
        -------
        :class:`astropy.table.QTable`


        Examples
        --------

        Print a few infos::

            >>> print(QXPTemplate.info([1, 32, 45]))
            Num          Name             z      ... allowedzlohigh         type
            --- --------------------- ---------- ... -------------- -------------------
              1 spDR2-001.fit         -0.0002435 ...          0.002             Stellar
             32 spDR2-032.fit                0.0 ...            5.5                 QSO
             45 spEigenGal-55740.fits        0.0 ...            1.5 Star-forming_galaxy

        """  # noqa: E501
        logLambda = np.arange(3.3, 4.0, 1e-4) * u.dex(u.Angstrom)  # dummy
        templates = QXPTemplate.read(logLambda, templateNumbers)

        rows = (
            {'Num': t.templateId,
             'Name': t.name,
             'z': t.redshift,
             'allowedzlow': t.allowedZRange[0],
             'allowedzlohigh': t.allowedZRange[1],
             'type': t.ttype}
            for t in templates
        )
        return QTable(rows=rows)
