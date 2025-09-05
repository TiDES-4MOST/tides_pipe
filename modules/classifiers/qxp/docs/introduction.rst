Introduction
============

This package is meant to be integrated into the extragalactic data
processing at CASU. Here we show only small examples for testing and
educational purposes. They assume that the code is checked out in the
current directory.


Running the pipeline for a single file
--------------------------------------

For testing purposes, one can run the redshift estimation code with a
single file. This looks like

.. code-block:: shell-session

    $ python3 -m qmostxp qmostxp/tests/qmost_00033300-2847587_20200913_100025_LJ1.fits
    qmost_00033300-2847587_20200913_100025_LJ1
    Template   Redshift   CrossCorr   ShiftIndex
          40    0.15001   10.286745   3143
          32    1.70583    4.677630   21615
          32    1.71312    4.292222   21673
          32    1.56969    3.940126   20494
          40    0.41307    3.923007   7616
    Probabilty on best match is 0.9999354560464898, FoM is 7.076834849653059

More than one input file can be given on the input line, then they are
processed in order. The file ``qmost_00033300-2847587_20200913_100025_LJ1.fits``
is provided with the sources for test purposes.


Create an output catalog from a directory
-----------------------------------------

The directory is a command line parameter. The output file will be
written to the current directory.

.. code-block:: shell-session

    $ python3 -m qmostxp qmostxp/tests/
    DEBUG:root:qmost_00033103-2820362_20200913_100030_LJ1.fits: z=0.15/0.7977
    DEBUG:root:qmost_00033300-2847587_20200913_100025_LJ1.fits: z=0.15/0.9999
    INFO:root:QXP completed, 2 total, 2 science, 1 good
    $ ls -l *.fits
    -rw-r--r-- 1 oles oles   23040  4. Mär 13:33 Qmost_1_20220304_l2qxp.fits
    $ showtable Qmost_1_20220304_l2qxp.fits --hdu PHASE3CATALOGUE
       TARGET_UID         TARGET_CNAME     TARGET_RA ...  z4CCSig  z4T_ID z4Type
                                              deg    ...                        
    ---------------- --------------------- --------- ... --------- ------ ------
    00033103-2820362 QMOST00033103-2820362      0.88 ...      4.20     32    QSO
    00033300-2847587 QMOST00033300-2847587      0.89 ...      3.94     32    QSO


More than one input directory can be specified. The directory
``qmostxp/tests`` contains two L1 spectra from OpR2 for test
purposes. *showtable* is provided as a part of Astropy.
