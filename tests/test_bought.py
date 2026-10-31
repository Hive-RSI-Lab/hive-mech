"""Bought parts (#47): the Hammond 1455 brick, panels, round SMA/BNC ports,
and BOUGHT parts left out of printing but kept in the fit check."""
import pytest

from mech import fit
from mech.bought import SERIES, Hammond1455, bought_names, panel
from mech.box import Tray, block
from mech.cli import printed_parts
from mech.kicad import Board, Part, Port, _KINDS, mates_from_above


def kind_of(name):
    return next((k for rx, k in _KINDS if rx.search(name)), "generic")


def test_sma_and_bnc_kinds():
    assert kind_of("SMA_Amphenol_901-144_Vertical") == "sma"   # (vertical: mates from above, never a port)
    assert mates_from_above("SMA_Amphenol_901-144_Vertical")
    assert kind_of("SMA_Samtec_SMA-J-P-X-ST-EM1_EdgeMount") == "sma"
    assert kind_of("BNC_Amphenol_031-5539_Horizontal") == "bnc"
    assert kind_of("D_SMA") == "sma" and kind_of("CONN-TH_XDJK-0271-1650") == "generic"  # only connectors get a kind


def rf_board(w=74.5, length=100.0):
    """A board for a 1455J1201: an edge-mount SMA (its thread 4 mm past the
    edge) and a USB-C (1 mm past) on its W edge."""
    t = 1.6
    sma = Part("J1", "SMA", "Connector_Coaxial:SMA_Edge", "top", 3.0, 20.0, 0.0,
               [-4.0, 16.5, 7.0, 23.5], [t, t + 6.5], "table", False)
    usb = Part("J2", "USB-C", "lib:USB-C", "top", 4.0, 50.0, 0.0, [-1.0, 45.5, 8.0, 54.5], [t, t + 3.3], "table", False)
    cap = Part("C1", "10u", "C_0805", "top", 50.0, 37.0, 0.0, [49.0, 36.0, 51.0, 38.0], [t, t + 1.3], "table", False)
    ports = [Port("J1", "W", [16.5, 23.5, t, t + 6.5], -4.0, 4.0, "top", "sma"),
             Port("J2", "W", [45.5, 54.5, t, t + 3.3], -1.0, 1.0, "top", "usb-c")]
    outline = [[0, 0], [length, 0], [length, w], [0, w]]
    return Board("test", t, outline, [], [length, w], [0, 0], [], [sma, usb, cap], ports)


def test_hammond_holds_the_board_and_its_ports_pass():
    b = rf_board()
    h = Hammond1455(b, "1455J1201", slot=1)
    assert h.z_floor == pytest.approx(-SERIES["1455J1201"]["slot_z"][1] - 0.2)
    parts = h.parts()
    r = fit.check(b, parts, skip=["hole_*", "screw_*"])
    bad = sorted(c["name"] for c in r["checks"] if not c["ok"])
    assert bad == [], fit.summary(r)
    # the SMA's hole in the front plate is round: a block the plug's size at its corner is still plate
    j1 = b.port("J1")
    u0, u1, z0, z1 = j1.opening()
    corner = block(h.x0 - 1.4, u1 - 0.6, z1 - 0.6, h.x0 - 0.1, u1 - 0.1, z1 - 0.1)
    assert fit._overlap(parts["front"], corner) > 0.9 * corner.volume


def test_the_card_guides_hold_the_board_edge():
    b = rf_board()
    h = Hammond1455(b, "1455J1201", slot=0)
    shell = h.shell()
    # the board's edge sits in a slot: free above and below it within the slot...
    assert fit._overlap(shell, fit.board_solid(b)) < fit.EPS
    # ...and a rib holds it from below and above, within the slot's depth
    y = -0.3   # past the board's S edge, inside the slot (the slots are 75 apart, the board 74.5)
    below = block(10, y - 0.5, -1.2, 20, y, -0.4)
    above = block(10, y - 0.5, b.thickness + 0.6, 20, y, b.thickness + 1.2)
    assert fit._overlap(shell, below) > 0.9 * below.volume
    assert fit._overlap(shell, above) > 0.9 * above.volume


def test_board_too_wide_or_narrow_is_refused():
    with pytest.raises(ValueError, match="at most"):
        Hammond1455(rf_board(w=75.0))
    with pytest.raises(ValueError, match="card guide"):
        Hammond1455(rf_board(w=60.0))
    with pytest.raises(ValueError, match="slot"):
        Hammond1455(rf_board(), slot=9)


def test_an_uncut_plate_blocks_the_ports():
    b = rf_board()
    h = Hammond1455(b)
    parts = h.parts(ports=False)
    r = fit.check(b, parts, only={"port_J1", "port_J2", "parts_clear"})
    # both bodies run into the uncut plate; the USB-C's plug is walled off too (the SMA's face is past it)
    assert sorted(c["name"] for c in r["checks"] if not c["ok"]) == ["parts_clear", "port_J2"]
    hits = next(c for c in r["checks"] if c["name"] == "parts_clear")["hits"]
    assert sorted(x["ref"] for x in hits) == ["J1", "J2"]


def test_round_port_in_a_tray_and_a_panel():
    b = rf_board()
    t = Tray(b)
    base = t.cut_ports(t.base())
    r = fit.check(b, {"base": base, "lid": t.lid()}, only={"port_J1"})
    assert r["ok"], fit.summary(r)
    p = panel(b, "W", t=1.6)   # on the SMA's face: the USB-C, 3 mm further in, sits too deep
    r = fit.check(b, {"panel": p}, only={"port_J1", "port_J2", "parts_clear"})
    assert sorted(c["name"] for c in r["checks"] if not c["ok"]) == ["port_J2"], fit.summary(r)
    p = panel(b, "W", t=1.6, at=-1.0)   # on the USB-C's face, the SMA's thread through it
    r = fit.check(b, {"panel": p}, only={"port_J1", "port_J2", "parts_clear"})
    assert r["ok"], fit.summary(r)


def test_bought_parts_are_checked_not_printed():
    class Mod:
        PRINT = {}
        BOUGHT = {"shell": "Hammond 1455J1201", "front": "its end plate"}
    assert bought_names(Mod) == {"shell", "front"}
    parts = {"shell": block(0, 0, 0, 5, 5, 5), "front": block(0, 0, 0, 1, 5, 5), "knob": block(0, 0, 0, 2, 2, 2)}
    assert list(printed_parts(Mod, parts)) == ["knob"]
    report = {"ok": True, "checks": [], "bought": {"shell": "Hammond 1455J1201", "front": "its end plate"},
              "material": {"shell": {"volume_cm3": 1, "solid_petg_g": 1}, "knob": {"volume_cm3": 0, "solid_petg_g": 0}}}
    md = fit.markdown(report)
    assert "## Bought" in md and "- shell: Hammond 1455J1201" in md and "- shell: 1 cm3" not in md
