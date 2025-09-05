#! /usr/bin/env python3
import os
from setuptools import setup

setup(use_scm_version={'write_to': os.path.join('qmostxp', 'version.py')})
