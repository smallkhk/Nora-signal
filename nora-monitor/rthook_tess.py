# Runtime hook: point pytesseract at the bundled tesseract.exe
import os, sys

if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    _tbin = os.path.join(_base, 'tesseract.exe')
    if os.path.isfile(_tbin):
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = _tbin
        except Exception:
            pass
        os.environ.setdefault('TESSDATA_PREFIX', _base)
