"""File-based logging. Safe when there is no console (pythonw / packaged GUI exe)."""
import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import user_config_dir

_logger = None


def get_logger():
    global _logger
    if _logger is not None:
        return _logger
    lg = logging.getLogger("trackball_daemon")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        fh = RotatingFileHandler(
            user_config_dir() / "daemon.log",
            maxBytes=512 * 1024, backupCount=2, encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(fh)
    except Exception:
        pass
    # Console handler only when a real stderr exists (absent under pythonw).
    if getattr(sys, "stderr", None):
        try:
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(sh)
        except Exception:
            pass
    _logger = lg
    return lg
