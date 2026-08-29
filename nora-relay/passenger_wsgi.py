import sys
import os

# Add virtualenv site-packages so flask can be found
venv_packages = '/home/ecliaoia/virtualenv/mon/3.11/lib/python3.11/site-packages'
if venv_packages not in sys.path:
    sys.path.insert(0, venv_packages)

sys.path.insert(0, '/home/ecliaoia/mon')
from relay import app as application
