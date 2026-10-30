"""The test-print coupon (mech.coupon): each of mech's clearances made at
sizes round the current guess, a plate that prints flat and fast, and the
person's answer written back into the fits the bricks use."""
import json
import os

import pytest

from mech import coupon, fits, slicer
from mech.fits import FITS


def test_every_fit_is_tried_round_its_guess():
    assert {t[0] for t in coupon.TESTS.values()} == set(fits.KEYS)
    for name, (key, sizes, _, _) in coupon.TESTS.items():
        assert sizes == sorted(sizes), name
        # the guess is tried with a size each side, so the print can move it either way
        assert sizes[0] < FITS[key] < sizes[-1], name
        assert FITS[key] in sizes, name
    got = {(f["test"], f["n"]) for f in coupon.features()}
    assert got == {(n, i + 1) for n, t in coupon.TESTS.items() for i in range(len(t[1]))}


def test_bricks_take_their_clearances_from_the_fits():
    import inspect
    from mech import printable
    from mech.box import Tray
    assert Tray.pilot_d == FITS["pilot_d"] and Tray.lip[2] == FITS["lip_gap"]
    sig = inspect.signature(Tray.screws_from_below).parameters
    assert sig["clear"].default == FITS["clear_d"] and sig["pilot"].default == FITS["pilot_d"]
    assert inspect.signature(Tray.button).parameters["clear"].default == FITS["button_clear"]
    assert inspect.signature(printable.check).parameters["max_bridge"].default == FITS["max_bridge"]


@pytest.fixture(scope="module")
def parts():
    return coupon.build()


def test_coupon_is_one_flat_print(parts):
    from build123d import Compound
    assert set(parts) == {"plate", "plug", "pin"}
    bb = Compound(children=list(parts.values())).bounding_box()
    assert bb.min.Z > -0.01 and bb.max.Z <= max(coupon.BOSS_H.values()) + 0.01
    assert bb.size.X < 100 and bb.size.Y < 80  # small: well inside any bed
    for name, p in parts.items():
        assert len(p.solids()) == 1, name  # the plate is one piece: nothing left floating
        assert p.bounding_box().min.Z > -0.01, name


def test_coupon_features_are_cut_to_size(parts):
    """Each socket and hole is there at its size: probe just inside and outside its edge."""
    from build123d import Vector
    plate = parts["plate"]
    z = coupon.PLATE / 2

    def solid(x, y, zz=z):
        return plate.is_inside(Vector(x, y, zz))
    for f in coupon.features():
        t, s, x, y = f["test"], f["size"], f["x"], f["y"]
        if t == "lip":
            half = coupon.LIP_W / 2 + s
            assert not solid(x + half - 0.03, y) and solid(x + half + 0.03, y), f
        elif t in ("button", "clear"):
            r = (coupon.PIN_D + 2 * s if t == "button" else s) / 2
            assert not solid(x + r - 0.03, y) and solid(x + r + 0.03, y), f
        elif t in ("pilot", "insert"):
            zz = coupon.BOSS_H[t] - 1
            assert not solid(x + s / 2 - 0.03, y, zz) and solid(x + s / 2 + 0.03, y, zz), f
        else:
            top = coupon.PLATE + coupon.BRIDGE_Z
            assert not solid(x, y, top - 0.1) and solid(x, y, top + 0.4), f
            assert solid(x - s / 2 - 1.2, y, top - 1), f  # a post holds each end
    # the plug's lip is the size the sockets are measured against
    px = coupon.X0 + coupon.LIP_W / 2
    assert parts["plug"].is_inside(Vector(px - 0.03, coupon.Y_LOOSE, 2.4))
    assert not parts["plug"].is_inside(Vector(px + 0.03, coupon.Y_LOOSE, 2.4))


def test_record_writes_the_sizes_that_fit(tmp_path):
    p = tmp_path / "fits.json"
    p.write_text(fits.PATH.read_text())
    got = coupon.record({"lip": 4, "pilot": 3}, "MK4, PETG", p=p, today="2026-10-30")
    assert got["lip_gap"] == coupon.TESTS["lip"][1][3] and got["pilot_d"] == coupon.TESTS["pilot"][1][2]
    assert got["clear_d"] == FITS["clear_d"]  # not tried: as it was
    again = coupon.record({"bridge": 1}, p=p, today="2026-11-01")
    assert again["calibrated"] == {"date": "2026-11-01", "note": "MK4, PETG",
                                   "picked": {"lip": 4, "pilot": 3, "bridge": 1}}
    assert fits.load(p) == json.loads(p.read_text())
    for bad in ({"lip": 7}, {"lid": 2}, {}):
        with pytest.raises(ValueError):
            coupon.record(bad, p=p)


def test_guide_names_every_test_and_how_to_answer():
    md = coupon.guide()
    for name in coupon.TESTS:
        assert f"| {name} |" in md
    assert "--record" in md


def test_coupon_prints_in_twenty_minutes(tmp_path):
    """The point of it: the person spends one short print, with no support."""
    if not slicer.find() and not os.environ.get("MECH_NEED_SLICER"):
        pytest.skip("no PrusaSlicer here (mech slice --install); CI installs it")
    got = coupon.make(tmp_path)["coupon"]
    assert "error" not in got, got
    assert got["time_s"] <= 20 * 60, got["time"]
    assert got["support_g"] == 0.0
    assert (tmp_path / "coupon.png").stat().st_size > 10_000
    assert "| lip |" in (tmp_path / "coupon.md").read_text()
