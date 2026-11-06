import os
from pathlib import Path
import sys


def application_root():
    if os.environ.get("SCREENKAFFEE_APPDIR"):
        return Path(os.environ["SCREENKAFFEE_APPDIR"]).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

