import sys
import os

VENV   = '/home/ecliaoia/virtualenv/mon/3.11'
INTERP = VENV + '/bin/python3'

if sys.executable != INTERP:
    os.execl(INTERP, INTERP, *sys.argv)

sys.path.insert(0, '/home/ecliaoia/mon')
from relay import app as application
