"""mech.kicad on a small hand-written board and on the Lite example's board.json."""
import json

import pytest

from conftest import FIXTURES, ROOT
from mech import sexpr
from mech.kicad import Board, _arc_points, _rot, load, read_board


def test_sexpr_reads_numbers_strings_and_nesting():
    t = sexpr.parse('(a 1 -2.5 "12" (b "x \\"y\\"") c)')
    assert t[0] == "a" and t[1] == 1.0 and t[2] == -2.5
    assert t[3] == "12" and isinstance(t[3], sexpr.Str)
    assert t[4] == ["b", 'x "y"'] and t[5] == "c"
    with pytest.raises(ValueError):
        sexpr.parse("(a (b)")


def test_rotation_matches_kicad():
    # KiCad turns +90 CCW on screen; with Y down, (1, 0) goes to (0, -1).
    x, y = _rot(1, 0, 90)
    assert abs(x) < 1e-9 and abs(y + 1) < 1e-9


def test_arc_passes_through_mid():
    pts = _arc_points((0, 1), (1, 0), (0, -1))
    assert max(p[0] for p in pts) == pytest.approx(1, abs=0.01)


@pytest.fixture(scope="module")
def tiny() -> Board:
    return read_board(FIXTURES / "tiny.kicad_pcb")


def test_outline_and_cutout(tiny):
    assert tiny.size == [50, 30]
    assert tiny.thickness == 1.6
    xs = [p[0] for p in tiny.outline]
    ys = [p[1] for p in tiny.outline]
    assert (min(xs), max(xs), min(ys), max(ys)) == (0, 50, 0, 30)
    assert len(tiny.cutouts) == 1
    cx = sum(p[0] for p in tiny.cutouts[0]) / len(tiny.cutouts[0])
    assert cx == pytest.approx(25, abs=0.01)


def test_holes_flip_y(tiny):
    holes = {h.ref: h for h in tiny.holes}
    assert (holes["H1"].x, holes["H1"].y) == (4, 26)
    assert (holes["H2"].x, holes["H2"].y) == (46, 4)
    assert holes["H1"].drill == 3.2 and holes["H1"].pad == 6.4
    assert not any(p.ref.startswith("H") for p in tiny.parts)


def test_parts_rotation_side_and_height(tiny):
    c1 = tiny.part("C1")
    assert c1.bbox == [31, 13, 39, 17]  # 4 x 8 courtyard turned 90 degrees
    assert c1.z == [1.6, 13.1] and c1.height_from == "name"
    assert c1.tht and c1.under == 2.5
    r1 = tiny.part("R1")
    assert r1.side == "bottom" and r1.z == [-0.9, 0] and r1.height_from == "table"
    assert tiny.bottom_depth() == 2.5
    assert tiny.top_height() == 13.1


def test_ports_and_top_access(tiny):
    assert [p.ref for p in tiny.ports] == ["J1"]
    j1 = tiny.port("J1")
    assert j1.edge == "N" and j1.kind == "usb-c" and j1.plane == 30
    assert j1.face[:2] == [15, 25]
    o = j1.opening()
    assert o[1] - o[0] == pytest.approx(12.4) and o[3] - o[2] >= 6.6
    assert tiny.top_access == ["SW1"]
    assert any("J1" in w for w in tiny.warnings)  # no model, no H<n>: a guess, said aloud


def test_json_round_trip(tiny, tmp_path):
    p = tmp_path / "board.json"
    p.write_text(tiny.to_json())
    back = load(p)
    assert back.to_dict() == json.loads(tiny.to_json())
    assert back.port("J1").opening() == tiny.port("J1").opening()


def test_lite_example_board():
    b = load(ROOT / "examples" / "hifi-lite" / "board.json")
    assert b.size == [100, 90]
    assert len(b.holes) == 4
    assert {p.ref: p.edge for p in b.ports} == {"J1": "N", "J2": "N", "J3": "N", "J4": "N"}
    assert b.port("J1").kind == "usb-c" and b.port("J4").kind == "terminal"
    # The stacked RCA jacks are the tallest part: 27 mm above the board, from
    # the enclosure worker's heights.json (their model agrees once scaled).
    assert b.part("J2").z[1] == pytest.approx(28.6) and b.part("J2").height_from == "override"
    assert b.part("C1").height_from == "model"


def test_heights_override_models_and_names(tmp_path):
    h = tmp_path / "heights.json"
    h.write_text('{"_doc": "x", "default": {"above": 1}, "C1": {"above": 20, "below": 1.0},'
                 ' "USB-C-SMD_TEST": {"above": 3.3}}')
    from mech.kicad import read_heights
    b = read_board(FIXTURES / "tiny.kicad_pcb", heights=read_heights(h))
    assert b.part("C1").z == [1.6, 21.6] and b.part("C1").under == 1.0
    assert b.part("C1").height_from == "override"
    assert b.part("J1").z == [1.6, 4.9]
    assert b.part("R1").height_from == "table"  # "default" doesn't override
    assert not any("J1" in w for w in b.warnings)
