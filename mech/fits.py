"""The clearances mech's bricks print with, measured on a real printer.

    from mech.fits import FITS
    FITS["lip_gap"]   # the lid lip's gap to the wall, per side (mm)

mech/fits.json holds them: the lid lip's gap, a button's clearance in its
hole, an M3 screw's clearance hole, an M3 self-tapper's pilot, an M3
heat-set insert's hole, and the longest bridge that prints flat. They start
as guesses (`"calibrated": null`); `mech coupon` makes a 20-minute test
print with each one graded, and `mech coupon --record` writes back what fit
(mech/coupon.py). MECH_FITS names another file (a second printer, a second
material).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PATH = Path(__file__).resolve().parent / "fits.json"
KEYS = ("lip_gap", "button_clear", "clear_d", "pilot_d", "insert_d", "max_bridge")


def path() -> Path:
    return Path(os.environ.get("MECH_FITS") or PATH)


def load(p: Path | None = None) -> dict:
    d = json.loads((p or path()).read_text(encoding="utf-8"))
    missing = [k for k in KEYS if k not in d]
    if missing:
        raise ValueError(f"{p or path()}: no {', '.join(missing)}")
    return d


FITS = load()
