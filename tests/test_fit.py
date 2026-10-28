"""The Tray bricks and the fit check, on the tiny board: a good box passes,
and each way of getting it wrong is caught by the check meant for it."""
import pytest

from conftest import FIXTURES
from mech import fit
from mech.box import Tray, block
from mech.kicad import read_board


@pytest.fixture(scope="module")
def board():
    return read_board(FIXTURES / "tiny.kicad_pcb")


def names_failing(report):
    return sorted(c["name"] for c in report["checks"] if not c["ok"])


def test_good_tray_passes(board):
    t = Tray(board)
    lid, btn = t.button(t.lid(), "SW1")
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": lid, "btn": btn})
    assert names_failing(r) == [], fit.summary(r)
    assert {x["ref"]: x["under"] for x in r["top_access"]} == {"SW1": ["btn"]}


def test_uncut_port_fails(board):
    t = Tray(board)
    r = fit.check(board, {"base": t.base(), "lid": t.lid()})
    assert names_failing(r) == ["port_J1"]


def test_port_cut_to_the_receptacle_only_fails_for_usb_c(board):
    # An opening just round the receptacle (no room for the plug's overmold).
    t = Tray(board)
    j = board.port("J1")
    u0, u1, z0, z1 = j.face
    base = t.base() - block(u0 - 0.3, 20, z0 - 0.3, u1 + 0.3, 40, z1 + 0.3)
    assert "port_J1" in names_failing(fit.check(board, {"base": base}))


def test_deep_recess_fails(board):
    t = Tray(board, gap=3.0)
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert names_failing(r) == ["port_J1"]
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": t.lid()}, max_recess={"J1": 6.0})
    assert names_failing(r) == []


def test_short_standoff_hits_leads(board):
    t = Tray(board, standoff=2.0)  # C1's leads stick out 2.5 mm
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert "parts_clear" in names_failing(r)
    hit = next(c for c in r["checks"] if c["name"] == "parts_clear")
    assert [h["ref"] for h in hit["hits"]] == ["C1"]


def test_low_lid_hits_tall_part(board):
    t = Tray(board, top_clear=-3.0)
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert "parts_clear" in names_failing(r)


def test_missing_bosses_and_small_bed(board):
    t = Tray(board)
    base = t.cut_ports(t.base())
    for h in board.holes:  # grind the bosses off
        base -= block(h.x - 5, h.y - 5, -t.standoff, h.x + 5, h.y + 5, 0.1)
    r = fit.check(board, {"base": base}, bed=(40, 40, 40))
    assert names_failing(r) == ["bed_base", "hole_H1", "hole_H2"]


def test_overlapping_parts_fail(board):
    t = Tray(board, top_clear=2.0)
    lid = t.lid()
    lid += block(10, 10, t.z_top - 1, 12, 12, t.z_top + 5)
    base = t.cut_ports(t.base()) + block(10, 10, t.z_top - 2, 12, 12, t.z_top + 1)
    r = fit.check(board, {"base": base, "lid": lid})
    assert "parts_apart" in names_failing(r)


def test_vents_and_window_cut_material(board):
    t = Tray(board)
    lid = t.lid()
    vented = t.vents(lid, (5, 5, 25, 15))
    assert vented.volume < lid.volume - 20
    floor = t.vents(t.base(), (10, 8, 20, 18), along="y", where="floor")
    assert floor.volume < t.base().volume - 5
    base = t.cut_ports(t.base())
    win = t.window(base, "SW1", "S")
    assert win.volume < base.volume
    assert fit.check(board, {"base": win, "lid": vented})["ok"]
