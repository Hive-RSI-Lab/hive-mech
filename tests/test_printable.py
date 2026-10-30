"""The printability checks: a part that prints without support passes, and
each way of needing support (a long bridge, a ledge or island in the air, a
steep overhang, a wall too thin to print) is caught by the check for it."""
import pytest

from conftest import FIXTURES
from mech import fit, printable
from mech.box import Tray, block, cylinder, teardrop
from mech.cli import printed_parts
from mech.kicad import read_board


@pytest.fixture(scope="module")
def board():
    return read_board(FIXTURES / "tiny.kicad_pcb")


def failing(part, **kw):
    return sorted(k for k, r in printable.check(part, **kw).items() if not r["ok"])


def wall_with_window(w):
    """A 2 mm wall, 30 high, standing on the bed, with a w-wide window 10 up."""
    return block(0, 0, 0, 40, 2, 30) - block(20 - w / 2, -1, 10, 20 + w / 2, 3, 15)


def test_plain_block_prints():
    assert failing(block(0, 0, 0, 20, 20, 10)) == []


def test_short_bridge_passes_long_one_fails():
    assert failing(wall_with_window(10)) == []
    r = printable.check(wall_with_window(24))
    assert not r["bridge"]["ok"] and r["bridge"]["worst_bridge_mm"] == pytest.approx(24, abs=0.6)
    assert failing(wall_with_window(24), max_bridge=30) == []


def test_ledge_in_the_air_fails():
    # A shelf sticking 3 mm out of a wall, 10 mm up.
    shelf = block(0, 0, 0, 20, 2, 20) + block(0, 2, 10, 20, 5, 12)
    r = printable.check(shelf)
    assert not r["bridge"]["ok"] and "ledge" in r["bridge"]["unsupported"][0]["why"]
    # The same shelf under a 45-degree chamfer prints.
    from build123d import Plane, Polygon, extrude
    face = Plane(origin=(0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)) * Polygon(
        (2, 7), (5, 10), (5, 12), (2, 12), align=None)
    chamfered = block(0, 0, 0, 20, 2, 20) + extrude(face, amount=20)
    assert failing(chamfered) == []


def test_island_in_mid_air_fails():
    part = block(0, 0, 0, 10, 10, 2) + cylinder(5, 5, 2, 8, 2) + block(0, 0, 8, 10, 10, 10)
    r = printable.check(part)
    assert not r["bridge"]["ok"]
    # The disc on the post is a ledge 4 mm out all round.
    assert any("ledge" in u["why"] for u in r["bridge"]["unsupported"])
    floating = block(0, 0, 0, 10, 10, 2) + block(0, 0, 5, 10, 10, 7)
    assert "nothing under it" in printable.check(floating)["bridge"]["unsupported"][0]["why"]


def test_steep_overhang_fails_45_degrees_and_small_holes_pass():
    from build123d import Plane, Polygon, extrude
    # A wall leaning out at 60 degrees from upright.
    face = Plane(origin=(0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)) * Polygon(
        (0, 0), (2, 0), (2 + 10 * 1.732, 10), (10 * 1.732, 10), align=None)
    assert failing(extrude(face, amount=10)) == ["overhang"]
    face = Plane(origin=(0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)) * Polygon(
        (0, 0), (2, 0), (12, 10), (10, 10), align=None)
    assert failing(extrude(face, amount=10)) == []
    wall = block(0, 0, 0, 40, 3, 20)
    assert failing(wall - _hole_y(10, 10, 5)) == []  # 5 mm round hole: fine
    assert failing(wall - _hole_y(20, 10, 12)) == ["overhang"]  # 12 mm round hole: its top sags
    assert failing(wall - teardrop(20, 1.5, 10, 12, 6)) == []  # the same as a teardrop prints


def _hole_y(x, z, d):
    from build123d import Cylinder, Pos, Rot
    return Pos(x, 1.5, z) * Rot(90, 0, 0) * Cylinder(d / 2, 10)


def test_thin_wall_fails():
    cup = block(0, 0, 0, 20, 20, 10) - block(0.5, 0.5, 1, 19.5, 19.5, 11)
    r = printable.check(cup)
    assert not r["wall"]["ok"] and r["wall"]["thin"][0][5] == pytest.approx(0.5, abs=0.1)
    assert failing(block(0, 0, 0, 20, 20, 10) - block(0.9, 0.9, 1, 19.1, 19.1, 11)) == []
    assert failing(cup, nozzle=0.25) == []  # a 0.25 nozzle's two lines fit in 0.5


def test_tray_prints_as_mech_build_turns_it(board):
    t = Tray(board)
    lid, btn = t.button(t.lid(), "SW1")
    parts = {"base": t.cut_ports(t.base()), "lid": lid, "btn": btn}

    class Mod:
        PRINT = {"lid": "flip", "btn": "flip"}
    r = fit.check(board, parts, printed=printed_parts(Mod, parts))
    names = {c["name"] for c in r["checks"]}
    assert {"overhang_lid", "bridge_base", "wall_btn"} <= names
    assert r["ok"], fit.summary(r)
    # Printed the way it sits, the lid's lip would hang in the air.
    r = fit.check(board, parts, printed=parts)
    assert "bridge_lid" in {c["name"] for c in r["checks"] if not c["ok"]}


def test_close_openings_merge_instead_of_leaving_a_sliver(board):
    t = Tray(board)
    base = t.cut_ports(t.base())
    j = board.port("J1")
    edge, u0, u1, z0, z1, a, b = t.opening(j)
    # A window 0.4 mm from the port's opening: cut as one with it.
    port = t._cuts[-1]
    near = (edge, u1 + 0.4, u1 + 4.4, z0, z1, a, b)
    base = t.open_wall(base, near)
    assert len(t._cuts) == 1 and t._cuts[0][2] == pytest.approx(u1 + 4.4) and port[1] == pytest.approx(u0)
    r = printable.check(base)
    assert r["wall"]["ok"], r["wall"]
