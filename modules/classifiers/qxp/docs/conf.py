import os
import sys
sys.path.insert(0, os.path.abspath('..'))
sys.path.append(os.path.abspath('.'))
from qmostxp import __version__ as qxp_version

project = 'QXP'
copyright = '2022, 4MOST'
author = 'Luke Davis, Ole Streicher'

# The full version, including alpha/beta/rc tags
release = qxp_version
# The short X.Y version
version = '.'.join(qxp_version.split('.')[:2])

master_doc = 'index'
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.doctest',
    'sphinx.ext.coverage',
    'sphinx.ext.mathjax',
    'sphinx.ext.intersphinx',
    'sphinx.ext.extlinks',
    'numpydoc',
    # 'matplotlib.sphinxext.plot_directive',
    # 'sphinxarg.ext',
    'sphinx_execute',
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    'astropy': ('http://docs.astropy.org/en/stable/', None),
    'numpy': ('https://docs.scipy.org/doc/numpy/',
              (None, 'http://data.astropy.org/intersphinx/numpy.inv')),
    'scipy': ('https://docs.scipy.org/doc/scipy/reference/',
              (None, 'http://data.astropy.org/intersphinx/scipy.inv')),
}

html_theme = "bootstrap-astropy"
html_theme_options = {
    'logotext1': 'IWG8',
    'logotext2': 'QXP',
    'logotext3': ':docs',
    'astropy_project_menubar': False
}
html_static_path = ['_static']

# These paths are either relative to html_static_path
# or fully qualified paths (eg. https://...)
html_css_files = [
    'brand.css',
]

numpydoc_show_class_members = False
autodoc_member_order = 'bysource'
autodoc_default_options = {
    # 'members': True,
    #'special-members': '__call__',
}

extlinks = {
    'gitlab': ('https://gitlab.4most.eu/iwg8/qxp/-/merge_requests/%s', '!'),
    'jira': ('https://4most-tickets.atlassian.net/browse/%s', ''),
    'adsabs':  ('https://ui.adsabs.harvard.edu/abs/%s', '[%s]'),
}
