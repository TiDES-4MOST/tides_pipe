Data exchange format description
================================

This section describes the data format that goes into the Data
eXchange Unit (DXU). The output of the 4XP pipeline is a catalog file
with three extensions:

 * Primary header
 * Catalogue table (extension ``PHASE3CATALOGUE``)
 * Provenance table (extension ``PHASE3PROVENANCE``)

Here, only the basic contents used in the implementation are
documented. For a full DXU description, see MST-TNO-PSC-20308-9238-0001_.

.. _MST-TNO-PSC-20308-9238-0001: https://ds-web.aip.de/docushare/dsweb/Get/Document-4319

.. _primary-header:

Primary header
--------------

The Primary header contains further information regarding the source
and processing of the data products that is not encoded in the
filename. It contains of ESO, 4MOST, and 4XP specific keywords:

.. exec::
    from qmostxp.makeDXU import primaryHeader
    print('.. list-table::')
    print('    :widths: auto')
    print('    :align: left')
    print('    :header-rows: 1')
    print('    :stub-columns: 1')
    print()
    print('    * - name')
    print('      - content')
    print('      - description')
    for l in primaryHeader.splitlines():
        if l.strip() == '':
    	    continue
        name, rem = l.split('=', 1)
	content, description = rem.split('/', 1)
	if '{' in content:
	    content = '*' + content.strip() + '*'
        print('    * - ' + name.strip())
	print('      - ' + content.strip())
	print('      - ' + description.strip())
    print()
    

.. _catalogue-table:

Catalogue table
---------------

The catalogue table is a FITS binary table that lists the results of
all successfully processed L1 spectra.

.. exec::
    from qmostxp.makeDXU import catalogTableColumns
    columns = ['name', 'description', 'dtype', 'unit', 'ucd', 'format', 'range']
    print('.. list-table::')
    print('    :widths: auto')
    print('    :align: left')
    print('    :header-rows: 1')
    print('    :stub-columns: 1')
    print()
    print('    * - ' + '\n      - '.join(columns))
    for name, row in catalogTableColumns.items():
        print('    * - ' + name)
	if 'range' in row:
	    row['range'] = f'{row["range"][0]}…{row["range"][1]}'
        for c in columns[1:]:
            print( '      - ' + row.get(c, ''))
    print()

.. _provenance-table:

Provenance table
----------------

Since the QXP catalogue has a large number of provenance records, the
provenance records are stored in an extra table. This table has just
one single column, ``PROV``, which lists all processed input files.
