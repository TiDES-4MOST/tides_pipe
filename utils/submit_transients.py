#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GENERAL DESCRIPTION
-------------------
This tool is an example of how to work with transients using a RESTful API
within the 4FS Web Interface. It provides both proof-of-concept python-based
routines for performing many actions, as well as rich commandline interface
for performing many actions from a terminal. The rest of this docstring does
describe many aspects of the tool, but you are implored to try the '--help'
input argument from the commandline yourself.

You'll see working examples for how to:
    - establish a connection to the API, using either your username+password or
      an access token
    - retrieve OPTIONS and a description of the API's schema
    - list known transients (with or without filters)
    - submit a new transient
    - update a specific transient
    - delete a specific transient

That is, the RESTful API provides a full CRUD service (i.e. Create, Retrieve,
Update, Delete) as covered by the points listed above, and this is possible
using a standard HTTP library and your preferred data-manipulation methods.

This tool uses the third-party "requests" package for making GETting/POSTing
requests from/to the 4FS_WI API. For more advanced usage, please see
https://requests.readthedocs.io/en/latest/user/quickstart/ and
https://requests.readthedocs.io/en/latest/user/advanced/.

The 4FS_WI transients API is based on the Django REST framework, and the URL
schema is based on https://www.django-rest-framework.org/api-guide/routers/#defaultrouter,
where {prefix} is the SCHEMA URL: https://4most.mpe.mpg.de/QFSwi/targetCat/transients/,
which is also defined below as an internal variable.

In general, use of the API requires a valid username+password as with the
standard use of the 4MOST 4FS Web Interface. Additionally, one may also use
an authentication token that allows access only to the transients API, which
may be preferred in lieu of hardcoding your secretive password. A routine and
CLI argument are available here for retrieving such aforementioned token.


METHODS OF INTERFACE
--------------------
This tool provides two options for use:
  a) call the tool directly from a shell and use its commandline interface (CLI).
  b) load the tool as a python module (or copy/paste routines to your own software); or,

In general, the CLI-based method always requires a valid JSON dictionary as the data
payload. Note here that JSON typically expects double-quotes for all string-based entries
within its keys/values. That is, this is valid JSON:
    {"key1":"val1_as_string", "key2":int2, ...}.
For some reason, the python routines always display dictionaries using single-quotes, e.g.:
    {'key1':'val1_as_string', 'key2':int2, ...}.
If you want to be able to copy/paste displayed dictionaries for use as JSON input, be sure
to use the "--no_single_quotes" CLI option, which effectively replaces all single-quotes
with double-quotes prior to processing the JSON payload.


TRANSIENT SUBMISSION
--------------------
In order to submit a (new) transient, one must either:
  a) use the CLI argument "--create" in addition to a "--data <JSON_PAYLOAD"; or,
  b) call the underlying create_transient() routine directly.
(remember to also provide username/password or access token credentials).

An example of the former would be:
    $ python submit_transients.py --token <TOKEN> --create --data "{'uploadedfor_survey_id':5,
      'name':'example_transient', 'resolution':1, 'subsurvey':'SUBSURVEY',
      'template':'s4250:g+1.5:m1.0:t02:z-0.50:a+0.00.AMBRE:ebv0.0.fits', 'ruleset':'HR_BLUE',
      'mag_type':'SDSS_r_AB'}" --no_single_quotes
    [...]
    2024-05-16 14:43:27,416 - 4fs-transients-1984667:INFO - create_transient() ran successfully:
    {'id': 855, 'uploadedfor_survey_id': 5, 'name': 'example_transient', 'ra': 0.0, 'dec': 0.0,
    'pmra': 0.0, 'pmdec': 0.0, 'epoch': 2021.0, 'resolution': 1, 'subsurvey': 'SUBSURVEY',
    'cadence': 0, 'template': 's4250:g+1.5:m1.0:t02:z-0.50:a+0.00.AMBRE:ebv0.0.fits',
    'ruleset': 'HR_BLUE', 'redshift_estimate': 0.0, 'redshift_error': 0.0, 'extent_flag': 0,
    'extent_parameter': 0.0, 'extent_index': 0.0, 'mag': 20.0, 'mag_err': 0.0,
    'mag_type': 'SDSS_r_AB', 'reddening': 0.0, 'date_earliest': 0.0, 'date_latest': 0.0,
    't_exp_d': 20.0, 't_exp_g': 15.0, 't_exp_b': 7.0, 't_exp_s': 4.0, 'template_redshift': 0.0,
    'cal_mag_blue': 0.0, 'cal_mag_green': 0.0, 'cal_mag_red': 0.0, 'cal_mag_err_blue': 0.0,
    'cal_mag_err_green': 0.0, 'cal_mag_err_red': 0.0, 'cal_mag_id_blue': '', 'cal_mag_id_green': '',
    'cal_mag_id_red': '', 'classification': 'TRA', 'completeness': 0.0, 'parallax': 0.0,
    'date_submitted': '2024-05-16T14:43:27.388126+02:00', 'date_modified':
    '2024-05-16T14:43:27.388164+02:00', 'date_ingested': None, 'is_active': True}

Note these important points:
- The resulting output which not only confirms to you that a new transient was created, but also
  includes its ID=855. This is a unique identifier for the transient within the transient API and
  you should keep track of this ID number in case you want to modify any of its properties or delete
  it in the future (see below).
- Any target properties which have not been explicitly specified via the data payload are assumed
  to be NULL and either set to empty strings or zeroes as needed.
- The date_* fields are read-only and not user-controllable. They exist only for the sake of
  data provenance and are made available to the user only as reference.
- The "uploadedfor_survey_id" field must always be explicitly set. Here, S10 must use
  uploadedfor_survey_id=15 and S16 must use uploadedfor_survey_id=46. In the examples above and
  below, uploadedfor_survey_id=5 uses a mock test survey "S_test" which is used only for internal
  tests/development by OpSys.


MODIFYING A SUBMISSION
----------------------
One may also modify a pre-existing transient submission but only under these conditions:
- any property of a transient may be modified so long as it has not yet been ingested by
  OpSys during the associated daily maintenance task (i.e. ca. 15:00 local time Germany); otherwise,
- in case the transient has already been ingested by OpSys into the pool of candidate
  "live targets" available for observation, then only the "is_active" flag may be modified

Assuming one of the conditions above are met, modification absolutely requires the ID value
of the desired transient. In this example, we will use ID=855 from above to modify its
parallax to be non-zero:
    $ python submit_transients.py --token <TOKEN> --update 855 --data "{'parallax':0.5}" --no_single_quotes
    [...]
    2024-05-16 15:04:20,788 - 4fs-transients-2064830:INFO - update_transient() ran successfully:
    {'id': 855, 'uploadedfor_survey_id': 5, 'name': 'example_transient', 'ra': 0.0, 'dec': 0.0,
    'pmra': 0.0, 'pmdec': 0.0, 'epoch': 2021.0, 'resolution': 1, 'subsurvey': 'SUBSURVEY',
    'cadence': 0, 'template': 's4250:g+1.5:m1.0:t02:z-0.50:a+0.00.AMBRE:ebv0.0.fits',
    'ruleset': 'HR_BLUE', 'redshift_estimate': 0.0, 'redshift_error': 0.0, 'extent_flag': 0,
    'extent_parameter': 0.0, 'extent_index': 0.0, 'mag': 20.0, 'mag_err': 0.0,
    'mag_type': 'SDSS_r_AB', 'reddening': 0.0, 'date_earliest': 0.0, 'date_latest': 0.0,
    't_exp_d': 20.0, 't_exp_g': 15.0, 't_exp_b': 7.0, 't_exp_s': 4.0, 'template_redshift': 0.0,
    'cal_mag_blue': 0.0, 'cal_mag_green': 0.0, 'cal_mag_red': 0.0, 'cal_mag_err_blue': 0.0,
    'cal_mag_err_green': 0.0, 'cal_mag_err_red': 0.0, 'cal_mag_id_blue': '', 'cal_mag_id_green': '',
    'cal_mag_id_red': '', 'classification': 'TRA', 'completeness': 0.0, 'parallax': 0.5,
    'date_submitted': '2024-05-16T15:02:25.384383+02:00',
    'date_modified': '2024-05-16T15:04:20.784853+02:00', 'date_ingested': None, 'is_active': False}


QUERYING EXISTING ENTRIES & APPLYING FILTERS
-------
Generally speaking, the API for transients supports not only data submission but also
data queries. This may be useful in case a survey wishes to retrieve data associated with
one or more previously-submitted transients. Without applying any filters to the set of
previously-submitted transients associated with any/all surveys accessible by the user,
one may use the CLI option "--list", e.g.:
    $ python submit_transients.py --token <TOKAN> --list
    2024-05-16 15:30:27,536 - 4fs-transients-2165364:INFO - **** STARTING A NEW SESSION (PID: 2165364) ****
    2024-05-16 15:30:27,538 - 4fs-transients-2165364:INFO - args were: Namespace(debug=False,
    url_schema=None, username=None, password=None, token=<TOKEN>, id=None, data=None, filter=None,
    request_token=False, get_visits=False, options=False, no_single_quotes=False, list=True,
    create=False, update=None, delete=None, usetutorialpayload=False, runtest=False)
    2024-05-16 15:30:27,775 - 4fs-transients-2165364:INFO - get_list() ran successfully
    {'id': 1, 'uploadedfor_survey_id': 5, 'name': 'NO_NAME', 'ra': 0.0, 'dec': 0.0, 'pmra': 0.0,
    'pmdec': 0.0, 'epoch': 2021.0, 'resolution': 1, 'subsurvey': 'SUBSURVEY', 'cadence': 0,
    [...]

This feature is most useful only when combined with filters. Filters may be applied to
the queryset, using an SQL-like grammar to optionally match a particular field against
a value.

Before describing the grammar in full detail, an example query which returns only the
set of transients which have IDs within a certain numerical range (e.g. 0<id<5) is:
    $ python submit_transients.py --token <TOKEN> --list --filter "id__gt=0&id__lt=5"
    2024-05-16 15:36:03,360 - 4fs-transients-2187080:INFO - **** STARTING A NEW SESSION (PID: 2187080) ****
    2024-05-16 15:36:03,362 - 4fs-transients-2187080:INFO - args were: Namespace(debug=False,
    url_schema=None, username=None, password=None, token=<TOKEN>, id=None, data=None,
    filter='id__gt=0&id__lt=5', request_token=False, get_visits=False, options=False,
    no_single_quotes=False, list=True, create=False, update=None, delete=None,
    usetutorialpayload=False, runtest=False)
    2024-05-16 15:36:03,421 - 4fs-transients-2187080:INFO - get_list() ran successfully
    [{'id': 1, 'uploadedfor_survey_id': 5, 'name': 'NO_NAME', 'ra': 0.0, 'dec': 0.0, 'pmra': 0.0,
    'pmdec': 0.0, 'epoch': 2021.0, 'resolution': 1, 'subsurvey': 'SUBSURVEY', 'cadence': 0,
    'template': 's4250:g+1.5:m1.0:t02:z-0.50:a+0.00.AMBRE:ebv0.0.fits', 'ruleset': 'HR_BLUE',
    'redshift_estimate': 0.0, 'redshift_error': 0.0, 'extent_flag': 0, 'extent_parameter': 0.0,
    'extent_index': 0.0, 'mag': 20.0, 'mag_err': 0.0, 'mag_type': 'Johnson_V_Vega',
    'reddening': 0.0, 'date_earliest': 0.0, 'date_latest': 0.0, 't_exp_d': 10.0, 't_exp_g': 10.0,
    't_exp_b': 10.0, 't_exp_s': 60.0, 'template_redshift': None, 'cal_mag_blue': None,
    'cal_mag_green': None, 'cal_mag_red': None, 'cal_mag_err_blue': None, 'cal_mag_err_green': None,
    'cal_mag_err_red': None, 'cal_mag_id_blue': None, 'cal_mag_id_green': None,
    'cal_mag_id_red': None, 'classification': 'TRA', 'completeness': None, 'parallax': None,
    'date_submitted': '2021-10-19T17:29:00.417704+02:00',
    'date_modified': '2021-10-19T17:29:00.417773+02:00', 'date_ingested': None, 'is_active': True},
    {'id': 2, 'uploadedfor_survey_id': 15, 'name': 'NO_NAME', 'ra': 165.46627262,
    'dec': -34.70473099, 'pmra': 0.0, 'pmdec': 0.0, 'epoch': 2021.0, 'resolution': 2,
    'subsurvey': 'tides-sn', 'cadence': 0,
    'template': 'SN_spec_specid1015_snt70_phase40_redshift0.964.fits', 'ruleset': 'tides_sn_max',
    'redshift_estimate': 0.0, 'redshift_error': 0.0, 'extent_flag': 0, 'extent_parameter': 0.0,
    'extent_index': 0.0, 'mag': 20.0, 'mag_err': 0.0, 'mag_type': 'Johnson_V_Vega',
    'reddening': 0.0, 'date_earliest': 0.0, 'date_latest': 0.0, 't_exp_d': 20.0, 't_exp_g': 20.0,
    't_exp_b': 20.0, 't_exp_s': 60.0, 'template_redshift': None, 'cal_mag_blue': None,
    'cal_mag_green': None, 'cal_mag_red': None, 'cal_mag_err_blue': None, 'cal_mag_err_green': None,
    'cal_mag_err_red': None, 'cal_mag_id_blue': None, 'cal_mag_id_green': None,
    'cal_mag_id_red': None, 'classification': 'TRA', 'completeness': None, 'parallax': None,
    'date_submitted': '2022-05-30T18:30:27.849082+02:00',
    'date_modified': '2022-05-30T18:30:27.849109+02:00', 'date_ingested': None, 'is_active': False},
    {'id': 3, 'uploadedfor_survey_id': 15, 'name': 'testSN1', 'ra': 165.46627262,
    'dec': -34.70473099, 'pmra': 0.0, 'pmdec': 0.0, 'epoch': 2021.0, 'resolution': 2,
    'subsurvey': 'tides-sn', 'cadence': 0,
    'template': 'SN_spec_specid1015_snt70_phase40_redshift0.964.fits', 'ruleset': 'tides_sn_max',
    'redshift_estimate': 0.0, 'redshift_error': 0.0, 'extent_flag': 0, 'extent_parameter': 0.0,
    'extent_index': 0.0, 'mag': 20.0, 'mag_err': 0.0, 'mag_type': 'Johnson_V_Vega',
    'reddening': 0.0, 'date_earliest': 0.0, 'date_latest': 0.0, 't_exp_d': 20.0, 't_exp_g': 20.0,
    't_exp_b': 20.0, 't_exp_s': 60.0, 'template_redshift': None, 'cal_mag_blue': None,
    'cal_mag_green': None, 'cal_mag_red': None, 'cal_mag_err_blue': None, 'cal_mag_err_green': None,
    'cal_mag_err_red': None, 'cal_mag_id_blue': None, 'cal_mag_id_green': None,
    'cal_mag_id_red': None, 'classification': 'TRA', 'completeness': None, 'parallax': None,
    'date_submitted': '2022-05-31T13:43:43.877783+02:00',
    'date_modified': '2022-05-31T13:43:43.877807+02:00', 'date_ingested': None, 'is_active': False},
    {'id': 4, 'uploadedfor_survey_id': 15, 'name': 'testSN2', 'ra': 165.46627262,
    'dec': -34.70473099, 'pmra': 0.0, 'pmdec': 0.0, 'epoch': 2021.0, 'resolution': 2,
    'subsurvey': 'tides-sn', 'cadence': 0,
    'template': 'SN_spec_specid1015_snt70_phase40_redshift0.964.fits', 'ruleset': 'tides_sn2022',
    'redshift_estimate': 0.0, 'redshift_error': 0.0, 'extent_flag': 0, 'extent_parameter': 0.0,
    'extent_index': 0.0, 'mag': 20.0, 'mag_err': 0.0, 'mag_type': 'Johnson_V_Vega',
    'reddening': 0.0, 'date_earliest': 0.0, 'date_latest': 0.0, 't_exp_d': 20.0, 't_exp_g': 20.0,
    't_exp_b': 20.0, 't_exp_s': 60.0, 'template_redshift': None, 'cal_mag_blue': None,
    'cal_mag_green': None, 'cal_mag_red': None, 'cal_mag_err_blue': None, 'cal_mag_err_green': None,
    'cal_mag_err_red': None, 'cal_mag_id_blue': None, 'cal_mag_id_green': None,
    'cal_mag_id_red': None, 'classification': 'TRA', 'completeness': None, 'parallax': None,
    'date_submitted': '2022-05-31T17:04:04.549486+02:00',
    'date_modified': '2022-05-31T17:04:04.549526+02:00', 'date_ingested': None, 'is_active': True}]

The grammar for such filters is potentially very powerful. The rest of this section that follows
presents various details that are available to the user for filtering queries. It is important
to note here that the CLI option "--filter" will pass any text verbatim to the second half of
the associated URL schema that is used for the API call. That is, in the examples below which
discuss something like:
    {URL_SCHEMA}/?{field}={value}
all text which would go after the question mark "?" will be passed directly. For example,
if a user specifies a filter as "--filter 'FOO_BAR_KEY=SOME_VAL'", the URL schema would be produced as:
    {URL_SCHEMA}/?FOO_BAR_KEY=SOME_VAL

Any string-/value-based field may be filtered directly, just note that
all field names are defined using strictly lowercase (as opposed to the
input file requirements using uppercase!):
    {URL_SCHEMA}/?{field}={value}
So, for example, any transients with the name matching "somefavoritename":
    {URL_SCHEMA}/?name=somefavoritename

The filtering may also use suffixes as part of the name of the field to
describe the "look-up type" (note the double-underscore between the field
name and its suffix!):
    {URL_SCHEMA}/?{field__lookuptype}={value}
Available lookuptype keywords are described under
https://docs.djangoproject.com/en/4.2/topics/db/queries/ and include:
    - "gt" and "lt" for greater-than and less-than
    - "gte" and "lte" for gt-or-equal and lt-or-equal
    - "startswith" or "istartswith" for checking against the beginning of a string
    - "endswith" or "iendswith" for checking against the ending of a string
    - "exact" or "iexact" for checking exactly (or) against a string
    - "contains" for checking within a string (or case-insensitive: "icontains")
    - "isnull" for looking for a missing value (if not empty or 0)
So, for example, one could collect all transients with non-zero date_latest:
    {URL_SCHEMA}/?date_latest__iexact=0

One may also chain such filters but only using logical AND via the
ampersand character, '&'. For example, to find entries with zero-valued
date_latest and declination at or above 0°:
    {URL_SCHEMA}/?date_latest=0&dec__gte=0
Currently, more advanced filtering is not available, however we could try
to implement something using another third-party plugin if needed, such as
this one: https://github.com/philipn/django-rest-framework-filters.

A note about the time zone-based fields: the date_submitted and date_modified
fields are automatically set during creation/modification. You can also
filter against them, but note that the local-to-Germany time zone is used
(i.e. UTC+1 or UTC+2). If you want to filter one of these fields without a
time zone, you can just use a value in a YYYY-MM-DD HH:MM[:ss[.uuuuuu]]
format. If you want to specify a time zone, you must add it at the end of
the date-time-stamp as "Z+XX" or "Z-XX" (or without a "+"). The requirement
of including "Z" is only relevant to URL-based API access and does not
affect the web-interface-based search form. Timestamps which do not include
a "Z" (and hence not time zone-aware) will automatically be matched with
the local time zone (Europe/Berlin), and timestamps which do include a
"Z" but without an offset will be assumed to be UTC.

As an example, if we consider three transients submitted at three times across
an afternoon:
    - "date_submitted": "2021-09-21T12:01:57.283596+02:00",
    - "date_submitted": "2021-09-21T14:01:57.283596+02:00",
    - "date_submitted": "2021-09-21T20:01:57.283596+02:00",
you could collect them all by querying any one of these (note that a "T"
is optional between the date and time):
    - {URL_SCHEMA}/?date_submitted__gt=2021-09-21
    - {URL_SCHEMA}/?date_submitted__gt=2021-09-21 12:00:00
    - {URL_SCHEMA}/?date_submitted__gt=2021-09-21T12:00:00Z02,
    - {URL_SCHEMA}/?date_submitted__gt=2021-09-21T12:00:00Z10,
but you wouldn't pick up the first one if you check against UTC:
    - {URL_SCHEMA}/?date_submitted__gt=2021-09-21T12:00:00Z+00.

--

Copyright (c) 2026 Jacob Laas <jclaas@mpe.mpg.de> & 4MOST <4most.eu>
Distributed under the MIT license:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished
to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
DEBUG = True
# standard library
import os
import sys
import logging, logging.handlers
import argparse
import time
from timeit import default_timer as timer
import distutils.version
import traceback as tb
import datetime
import pprint
import json
# third-party
import requests
from requests.auth import HTTPBasicAuth
# local
pass

# set up logging functionality
# NOTE: only logging to terminal!
logformat = '%(asctime)s - %(name)s:%(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=logformat)
log = logging.getLogger("4fs-transients-%s" % os.getpid())
if DEBUG:
    log.setLevel(logging.DEBUG)
log.info("**** STARTING A NEW SESSION (PID: %s) ****" % os.getpid())

### define parameters and client
# URL_SCHEMA = "http://127.0.0.1:8080/targetCat/transients/"
URL_SCHEMA = "https://4most.mpe.mpg.de/QFSwi/targetCat/transients/"
DEFAULT_MAX_ITEMS = 10
USERNAME = None
PASSWORD = None
ACCESS_TOKEN = None
pp = pprint.PrettyPrinter(indent=4, compact=True)

### define an example payload and provide routine for usefully renaming it
DEFAULT_PAYLOAD = {
    "uploadedfor_survey_id": 5,
    "name": "NO_NAME",
    "ra": 0.0,
    "dec": 0.0,
    "pmra": 0.0,
    "pmdec": 0.0,
    "epoch": 2021,
    "resolution": 1,
    "subsurvey": "SUBSURVEY",
    "cadence": 0,
    "template": "TEMPLATE_NAME",
    "ruleset": "RULESET_NAME",
    # "redshift_estimate": None,
    # "redshift_error": None,
    "extent_flag": 0,
    "extent_parameter": 0,
    "extent_index": 0,
    "mag": 20,
    # "mag_err": None,
    "mag_type": "MAG_TYPE",
    # "reddening": None,
    # "date_earliest": None,
    # "date_latest": None,
    "t_exp_d": 20.0,
    "t_exp_g": 15.0,
    "t_exp_b": 7.0,
    "t_exp_s": 4.0,
    # "template_redshift": None,
    # "cal_mag_blue": None,
    # "cal_mag_green": None,
    # "cal_mag_red": None,
    # "cal_mag_err_blue": None,
    # "cal_mag_err_blue": None,
    # "cal_mag_err_green": None,
    # "cal_mag_id_green": None,
    # "cal_mag_id_red": None,
    # "cal_mag_id_red": None,
    "classification": "TRA",
    # "completeness": None,
    # "parallax": None,
    "is_active": True,
}

### helper routines

def modified_payload(payload=None):
    """
    This is a helper routine for simply copying some default payload
    and renaming the target's name to be something useful for unique
    identification.

    Parameters
    ----------
    payload : dict, optional
        an alternative payload of data defining a new target

    Returns
    -------
    dict
        a payload of data
    """
    if payload is None:
        payload = dict(DEFAULT_PAYLOAD)
    else:
        payload = dict(payload)  # to avoid modifying the original
    # update with a new name
    payload.update({
        "name": datetime.datetime.now().strftime(format="%Y%m%d %H%M%S.%f").replace(".", " ")
    })
    # return it
    return payload

def get_session(username=USERNAME, password=PASSWORD, token=ACCESS_TOKEN):
    """
    This routine simply provides a session for the new request, which
    makes it easier to systematically update the authentication credentials.

    Parameters
    ----------
    username : str, optional
        the username to use
    password : str, optional
        the password to use
    token : str, optional
        the access token to use

    Returns
    -------
    requests.Session
        the session for the new request
    """
    s = requests.Session()
    if (username is not None) and (password is not None):
        s.auth = (username, password)
    if (token is not None):
        s.headers.update({"Authorization": "Token %s" % (token,)})
    return s

def check_request(request=None, caller="(caller N/A)", printout=False, return_mode=None):
    """
    Processes the results of the request and returns data according to ``return_mode``.

    Supported ``return_mode`` values:
        - "raw" / "response" : return the original ``requests.Response`` object or text.
        - "listdict"        : (default) return a list of dicts or a dict as‑is.
        - "json"            : return a JSON‑encoded ``str`` of the parsed data.
        - "pp"              : pretty‑print the result (using ``pp.pprint``) and return ``None``.
    """
    if request is None:
        return None

    # “response” bypass any processing
    if return_mode in ("response", ):
        return request
    elif return_mode in ("raw", ):
        return request.text

    status_ok = request.status_code in (requests.codes.ok, 204)

    try:
        if status_ok:
            # Try to parse JSON –‑ API returns a dict or a list of dicts
            try:
                parsed = json.loads(request.text) if request.text else None
                if isinstance(parsed, dict):
                    parsed = [parsed]          # normalise single‑object responses
            except Exception:
                parsed = None

            result = parsed if parsed is not None else request.text

            # ---------- return‑mode handling ----------
            if return_mode == "pp":
                # Human‑friendly CLI output
                if isinstance(result, (dict, list)):
                    pp.pprint(result)
                else:
                    print(result)
                return None

            if return_mode == "json":
                # Return a JSON string (useful for programmatic consumption)
                return json.dumps(result)

            # Default –‑ “listdict” (or any unknown mode)
            if printout:
                log.info(f"{caller} ran successfully: {result}")
            else:
                log.debug(f"{caller} ran successfully")
            return result

        # ---------- error path ----------
        err_text = None
        try:
            parsed_err = json.loads(request.text)
            if isinstance(parsed_err, dict) and "err" in parsed_err:
                err_text = str(parsed_err.get("err"))
            else:
                err_text = request.text
        except Exception:
            err_text = request.text or f"{caller} failed with HTTP code {request.status_code}"

        log.warning(err_text)

        if return_mode == "pp":
            print(err_text)
            return None
        return err_text

    except Exception:
        e = sys.exc_info()
        log.warning(f"{caller} encountered an unexpected error: {e[1]}")
        return str(e[1]) if e[1] else str(e[0])

### main routines

def get_api_token(printout=True, timeout=15, return_mode="listdict"):
    """
    Returns the queried authentication token for the transients API.

    Note that this is currently the only way to collect it (short of
    contacting the 4FS_WI administrator directly).

    Parameters
    ----------
    printout : bool, optional, default=True
        forwarded to check_request(), see docstring above
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)

    Returns
    -------
    str or requests.Response
        either a) normally the queried API token as a string, or b) the HTTP response in case of errors

    Notes
    -----
    In case of an error, the full requests.Response is returned. In that case,
    one should reference https://docs.python-requests.org/en/latest/api/ for
    how to proceed. Typically the error message will be the first set of lines
    within r.text, and the r.status_code will not be 2XX but more like a value
    or 4- or 5-hundred-something. A full list of possbile HTTP response codes
    is available here: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status.
    """
    url = URL_SCHEMA
    username = USERNAME
    password = PASSWORD
    token = ACCESS_TOKEN
    url = url.replace("targetCat/transients", "transients-token-auth")
    session = get_session(username=username, password=password, token=token)
    r = session.post(
        url,
        data={"username": username, "password": password},
        timeout=timeout,
    )
    return check_request(request=r, caller="get_api_token()", printout=printout, return_mode=return_mode)

def get_visits(printout=True, timeout=15, return_mode="listdict"):
    """
    Returns the candidate visits (+ tiles) via the transients API.

    Note that this is currently the only way to collect it (short of
    contacting the 4FS_WI administrator directly).

    Parameters
    ----------
    printout : bool, optional, default=True
        forwarded to check_request(), see docstring above
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)

    Returns
    -------
    str or requests.Response
        either a) normally the queried API token as a string, or b) the HTTP response in case of errors
    """
    url = URL_SCHEMA
    username = USERNAME
    password = PASSWORD
    token = ACCESS_TOKEN
    url = url.replace("targetCat/transients", "targetCat/get-candidate-visits")
    session = get_session(username=username, password=password, token=token)
    r = session.get(
        url,
        timeout=timeout,
    )
    return check_request(request=r, caller="get_visits()", printout=printout, return_mode=return_mode)

def get_options(printout=True, timeout=15, return_mode="listdict"):
    """
    Simply prints (optional) and returns the list of OPTIONS describing
    the API schema.

    Parameters
    ----------
    printout : bool, optional, default=True
        forwarded to check_request(), see docstring above
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)

    Returns
    -------
    str
        the results from the OPTIONS query
    """
    url = URL_SCHEMA
    username = USERNAME
    password = PASSWORD
    token = ACCESS_TOKEN
    session = get_session(username=username, password=password, token=token)
    r = session.options(
        url,
        timeout=timeout,
    )
    return check_request(request=r, caller="get_options()", printout=printout, return_mode=return_mode)

def get_list(pk=None, flt=None, limit=None, timeout=15, return_mode="listdict"):
    """
    Returns a queried list of transients.

    Note that the list contains ALL the elements available, within the user's
    survey membership, however, additional filters may be applied to select
    any particular subset.

    Parameters
    ----------
    pk : int, optional, default=None
        the id of an individual transient (if not a set)
    flt : str, optional, default=None
        a (set of) field+lookup pair(s) for applying selection filters
    limit : int, optional, default=10
        the maximum number of items to retrieve in a query
        NOTE: this will not override a filter if you have already set limit=<blah> in the filter itself
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)

    Returns
    -------
    str or requests.Response
        either a) normally the queried transient (or set thereof) in format JSON, or b) the HTTP response in case of errors

    Notes
    -----
    In case of an error, the full requests.Response is returned. In that case,
    one should reference https://docs.python-requests.org/en/latest/api/ for
    how to proceed. Typically the error message will be the first set of lines
    within r.text, and the r.status_code will not be 2XX but more like a value
    or 4- or 5-hundred-something. A full list of possbile HTTP response codes
    is available here: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status.
    """
    url = URL_SCHEMA
    username = USERNAME
    password = PASSWORD
    token = ACCESS_TOKEN
    if pk is not None:
        url = "%s/%s/" % (url.rstrip("/"), pk)
    if flt is not None:
        url = "%s/?%s" % (url.rstrip("/"), flt)
    if limit is not None:
        if flt is None:
            flt = f"limit={limit}"
        elif "limit=" in flt:
            pass
        else:
            flt = f"{flt}&limit={limit}"
    session = get_session(username=username, password=password, token=token)
    r = session.get(
        url,
        timeout=timeout,
    )
    return check_request(request=r, caller="get_list()", printout=False, return_mode=return_mode)

def create_transient(data=None, printout=True,
                     no_single_quotes=False,
                     timeout=15,
                     return_mode="listdict"):
    """
    Submits a new transient.

    Note that by default, a payload containing mostly zeroes is used to describe
    the new transient, defined by the dictionary DEFAULT_PAYLOAD. Alternatively,
    you can provide your own dictionary (or list thereof) describing the new
    transient(s).

    Parameters
    ----------
    data : dict or list, optional, default=None
        the dict (or list thereof) describing the new transient
    printout : bool, optional, default=True
        whether to print out to the terminal the post-submission HTTP response
    no_single_quotes : bool, optional, default=False
        whether to replace all single-quotes with double-quotes (i.e. useful for pre-formatting JSON data)
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)

    Returns
    -------
    str or requests.Response
        the post-submission HTTP response
    """
    url = URL_SCHEMA
    username = USERNAME
    password = PASSWORD
    token = ACCESS_TOKEN
    if data is None:
        data = modified_payload()
    elif isinstance(data, (dict, list)):
        pass
    else:
        if no_single_quotes:
            data = data.replace("'", '"')
        data = json.loads(data)
        if isinstance(data, list):
            raise SyntaxError("the JSON produced from the data payload should be a single dictionary and not a list!")
        log.debug(f"json payload looks like: {data}")
        payload = dict(DEFAULT_PAYLOAD)
        payload.update(data)
        data = payload
        del payload
    # data = json.dumps(data)
    session = get_session(username=username, password=password, token=token)
    r = session.post(
        url,
        json=data,
        timeout=timeout,
    )
    return check_request(request=r, caller="create_transient()", printout=printout, return_mode=return_mode)

def update_transient(pk=None,
                     data=None,
                     printout=True,
                     no_single_quotes=False,
                     timeout=15,
                     return_mode="listdict"):
    """
    Updates a current transient with new data. Note that this routine uses
    strictly the PATCH method to provide a partial update to a transient,
    which means you can provide all the new fields or simply a subset thereof.

    Parameters
    ----------
    pk : int
        the id of an individual transient
    data : dict
        the dict describing the data to use during the update
    printout : bool, optional, default=True
        whether to print out to the terminal the post-submission HTTP response
    no_single_quotes : bool, optional, default=False
        whether to replace all single-quotes with double-quotes (i.e. useful for pre-formatting JSON data)
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)

    Returns
    -------
    str or requests.Response
        the post-submission HTTP response
    """
    url = URL_SCHEMA
    username = USERNAME
    password = PASSWORD
    token = ACCESS_TOKEN
    if pk is None:
        return
    else:
        url = "%s/%s/" % (url.rstrip("/"), pk)
    if data is None:
        data = modified_payload()
    elif isinstance(data, dict):
        pass
    else:
        if no_single_quotes:
            data = data.replace("'", '"')
        data = json.loads(data)
    session = get_session(username=username, password=password, token=token)
    r = session.patch(
        url,
        json=data,
        timeout=timeout,
    )
    return check_request(request=r, caller="update_transient()", printout=printout, return_mode=return_mode)

def delete_transient(pk=None, printout=True, timeout=15, return_mode="listdict"):
    """
    Deletes a current transient.

    Note that this is NOT ALLOWED if the transient has already been ingested by
    OpSys into their OSTD (OpSys Target Database). If the transient has already
    been ingested, it must instead be modified from "is_active=True" to
    "is_active=False".

    Parameters
    ----------
    pk : int
        the id of an individual transient
    printout : bool, optional, default=True
        whether to print out to the terminal the post-submission HTTP response
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)

    Returns
    -------
    str or requests.Response
        the post-submission HTTP response
    """
    url = URL_SCHEMA
    username = USERNAME
    password = PASSWORD
    token = ACCESS_TOKEN
    if pk is None:
        return
    else:
        url = "%s/%s/" % (url.rstrip("/"), pk)
    session = get_session(username=username, password=password, token=token)
    r = session.delete(
        url,
        timeout=timeout,
    )
    return check_request(request=r, caller="delete_transient()", printout=printout, return_mode=return_mode)

### test routine(s)

def test_multi_simul(timeout=15, return_mode="text"):
    """
    Bulds and submits TWO transients at once. (development/testing only)

    Parameters
    ----------
    timeout : int or None, optional, default=15
        the timeout to use for the remote connection (if not None, must be number of seconds)
    """
    # build payload
    multiple_transients = []
    multiple_transients.append(modified_payload())
    multiple_transients.append(modified_payload())
    assert len(multiple_transients) == 2
    log.debug("payload: %s" % (multiple_transients,))
    # submit all at once
    time_submission = datetime.datetime.now()
    timer_submission_start = timer()
    result = create_transient(data=multiple_transients, timeout=timeout, return_mode=return_mode)
    print("\n\n\n\n\nretrieved from create_transient():", result, "\ntype:", type(result), "\n")

    timer_submission_stop = timer()
    time_to_submit = timer_submission_stop - timer_submission_start
    # try to catch them
    time_submission_str = time_submission.strftime("%Y-%m-%dT%H:%M:%S")
    log.debug("time_submission_str: %s" % (time_submission_str,))
    result = get_list(flt=f"date_submitted__gt={time_submission_str}", return_mode=return_mode)

    print("\n\n\n\n\nretrieved from get_list():", result, "\ntype:", type(result), "\n")
    return result

def run_test():
    """
    This routine just provides code which can be quickly edited
    for testing something in the same tool. It serves as a placeholder
    in case the user wants to edit this tool and run their own test(s),
    in which case the function call below should be replaced with
    other python of interest.

    This routine is activated with the "--runtest" command-line argument.
    """
    test_multi_simul()



if __name__ == '__main__':
    """Set up the argument parser for CLI usage."""

    ### define input arguments
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "-d", "--debug", action='store_true',
        help="whether to add extra print messages to the terminal"
    )
    # schema/auth items
    parser.add_argument(
        "--url_schema", type=str, default=None,
        help="overrides the URL schema for the API"
    )
    parser.add_argument(
        "--username", type=str, default=None,
        help="sets a username for the API"
    )
    parser.add_argument(
        "--password", type=str, default=None,
        help="sets a password for the API"
    )
    parser.add_argument(
        "--token", type=str, default=None,
        help="sets an access token for the API"
    )
    # control element details
    parser.add_argument(
        "--id", type=int, nargs="?",
        help="the ID of the transient"
    )
    parser.add_argument(
        "--data", type=str, default=None,
        help="allows you provide JSON-like data for overriding the default payload"
    )
    parser.add_argument(
        "--timeout", type=str, default=15,
        help="the timeout to use for remote connections (both 'connect' and 'read'), can be 'None' or some integer in units of seconds"
    )
    parser.add_argument(
        "--filter", type=str, default=None,
        help="sets a filter (field+lookuptype) to the URL schema (list-only!)"
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_MAX_ITEMS,
        help=f"limits a list query to a maximum number of items (list-only!) (default: {DEFAULT_MAX_ITEMS})"
    )
    # standard actions
    parser.add_argument(
        "--request-token", action='store_true',
        help="requests the auth token for the API (NOTE: requires a valid user+password combination)"
    )
    parser.add_argument(
        "--get-visits", action='store_true',
        help="requests the candidate visits (and tiles)"
    )
    parser.add_argument(
        "--options", action='store_true',
        help="simply returns the OPTIONS describing the schema"
    )
    parser.add_argument(
        "--no_single_quotes", action='store_true',
        help="whether to convert all single-quotes to double-quotes (useful if copying/pasting a queried entry)"
    )
    parser.add_argument(
        "--list", action='store_true',
        help="simply returns the full list of transients"
    )
    parser.add_argument(
        "--create", action='store_true',
        help="creates a new transient"
    )
    parser.add_argument(
        "--update", type=int, default=None,
        help="updates the transient matching the primary key (pk == id)"
    )
    parser.add_argument(
        "--delete", type=int, default=None,
        help="deletes the transient matching the primary key (pk == id)"
    )
    # dev-related controls
    parser.add_argument(
        "--usetutorialpayload", action='store_true',
        help="(dev only) fixes the transient payload to be compatible with the 4FS_WI Tutorial files"
    )
    parser.add_argument(
        "--runtest", action='store_true',
        help="(dev only) launch the run_test() routine instead of the standard routines"
    )
    parser.add_argument(
        "--return-mode", type=str, default="pp", choices=("json", "listdict", "raw", "response", "pp"),
        help="controls returned data: default is 'pp' (CLI) or 'listdict' (python) on success (string on error), 'json'=JSON from listdict, 'raw'=raw body, 'response'=requests.Response, 'pp'=Pretty-Print (i.e. returns None)"
    )

    ### parse arguments
    args = parser.parse_args()
    if args.debug and not DEBUG:
        log.info("activating debugging mode via CLI")
        log.setLevel(logging.DEBUG)
        DEBUG = True
    log.debug("args were: %s" % (args,))
    if args.url_schema is not None:
        URL_SCHEMA = args.url_schema
    if args.username is not None:
        USERNAME = args.username
    if args.password is not None:
        PASSWORD = args.password
    if args.token is not None:
        ACCESS_TOKEN = args.token
    try:
        args.timeout = int(args.timeout)
    except Exception as m:
        args.timeout = None

    ### override payload if desired
    if args.usetutorialpayload:
        DEFAULT_PAYLOAD.update({
            "ra": 165.46627262,
            "dec": -34.70473099,
            "subsurvey": "sub1",
            "template": "s4250:g+1.5:m1.0:t02:z-0.50:a+0.00.AMBRE:ebv0.0.fits",
            "ruleset": "HR_BLUE",
            "mag_type": "Johnson_V_Vega",
            "resolution": 2,
            "t_exp_b": 60,
            "t_exp_d": 45,
            "t_exp_g": 22,
            "t_exp_s": 12,
        })

    ### launch one of the main routines after everything else is complete
    if args.runtest:
        run_test()
    if args.request_token:
        get_api_token(timeout=args.timeout, return_mode=args.return_mode)
    elif args.get_visits:
        get_visits(timeout=args.timeout, return_mode=args.return_mode)
    elif args.options:
        get_options(timeout=args.timeout, return_mode=args.return_mode)
    elif args.list:
        get_list(pk=args.id, flt=args.filter, limit=args.limit, timeout=args.timeout, return_mode=args.return_mode)
    elif args.create:
        create_transient(data=args.data,
                         no_single_quotes=args.no_single_quotes,
                         timeout=args.timeout,
                         return_mode=args.return_mode)
    elif args.update:
        update_transient(pk=args.update, data=args.data,
                         no_single_quotes=args.no_single_quotes,
                         timeout=args.timeout,
                         return_mode=args.return_mode)
    elif args.delete:
        delete_transient(pk=args.delete,
                         timeout=args.timeout,
                         return_mode=args.return_mode)

    sys.exit()
