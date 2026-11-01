"""The Tray bricks and the fit check, on the tiny board: a good box passes,
and each way of getting it wrong is caught by the check meant for it."""
import pytest

from conftest import FIXTURES
from mech import fit
from mech.box import Tray, block, cylinder
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


def l_shaped(board):
    """The tiny board with its NE corner (x > 40, y > 20) cut away."""
    import dataclasses
    return dataclasses.replace(board, outline=[[0, 0], [50, 0], [50, 20], [40, 20], [40, 30], [0, 30]])


def test_cavity_follows_a_shaped_outline(board):
    shaped = l_shaped(board)
    t = Tray(shaped)
    base, lid = t.cut_ports(t.base()), t.lid()
    r = fit.check(shaped, {"base": base, "lid": lid})
    assert names_failing(r) == [], fit.summary(r)
    zs = (t.z_floor + 1, t.z_top - 1)
    wall = block(41.0, 24, zs[0], 42.4, 27, zs[1])  # just past the notch's W edge (x = 40)
    corner = block(44, 24, t.z_bottom - 1, 47, 27, t.z_top + t.lid_t + 1)  # well inside the cut-away
    assert (base & wall).volume > 0.99 * wall.volume  # the wall turns into the notch
    assert fit._overlap(base, corner) < fit.EPS and fit._overlap(lid, corner) < fit.EPS  # nothing over it
    boxed = Tray(shaped, shape="box")
    assert fit._overlap(boxed.base(), wall) < fit.EPS  # the bounding box left it hollow


def test_box_shape_still_builds(board):
    t = Tray(board, shape="box")
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert names_failing(r) == [], fit.summary(r)
    with pytest.raises(ValueError):
        Tray(board, shape="round")


def test_lid_short_of_the_cavity_fails(board):
    t = Tray(board)
    base = t.cut_ports(t.base())
    short = t.lid() - block(-10, -10, t.z_top - 10, 60, 5.0, t.z_top + 10)  # 5.6 mm missing at the S wall
    r = fit.check(board, {"base": base, "lid": short})
    assert names_failing(r) == ["lid_covers"], fit.summary(r)
    gap = next(c for c in r["checks"] if c["name"] == "lid_covers")
    assert gap["open_mm2"] > 100 and gap["where"][0][1] < 0 < gap["where"][0][3]


def test_lid_from_a_smaller_plan_fails(board):
    # The 10-28 core_box bug: the base built round a bigger cavity than the lid's.
    base = Tray(board, shape="box", gap=4.0).base()
    r = fit.check(board, {"base": base, "lid": Tray(board, shape="box").lid()})
    assert "lid_covers" in names_failing(r)


def test_lid_inside_the_walls_covers(board):
    # A plate dropped inside the walls with a 0.25 mm fit gap: the ring round it isn't a hole.
    t = Tray(board)
    plate = t.plan(-0.25, t.z_top - t.lid_t, t.z_top)
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": t.vents(plate, (5, 5, 25, 15))})
    assert "lid_covers" not in names_failing(r), fit.summary(r)


def test_tray_with_its_own_inner_is_a_box(board):
    class Wide(Tray):
        @property
        def inner(self):
            w, h = self.board.size
            return (-0.6, -6.0, w + 0.6, h + 0.6)  # room in front of the S edge
    t = Wide(board)
    assert t.shape == "box"
    lid = t.lid()
    assert lid.bounding_box().min.Y < -7.9  # the lid reaches the moved S wall
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": lid})
    assert "lid_covers" not in names_failing(r), fit.summary(r)
    with pytest.raises(ValueError):
        Wide(board, shape="outline")


def test_added_box_inside_a_part_fails(board):
    import copy
    b = copy.deepcopy(board)
    t = Tray(b)
    b.add_box("HS1", 33, 13, 10, 37, 17, 14, "heatsink over C1")  # C1 is 13 mm tall there
    b.add_box("NUT1", 41, 20, 2, 45, 23, 6, "a nut in free space")
    r = fit.check(b, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert "added_parts_clear" in names_failing(r), fit.summary(r)
    c = next(c for c in r["checks"] if c["name"] == "added_parts_clear")
    assert c["clashes"] == [["HS1", "C1"]] and c["added"] == 2
    b.parts = [p for p in b.parts if p.ref != "HS1"]
    r = fit.check(b, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert "added_parts_clear" not in names_failing(r), fit.summary(r)


def test_nothing_added_no_added_check(board):
    t = Tray(board)
    r = fit.check(board, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert "added_parts_clear" not in [c["name"] for c in r["checks"]]


def test_screws_from_below_into_lid_pillars(board):
    t = Tray(board)
    base, lid = t.screws_from_below(t.cut_ports(t.base()), t.lid())
    r = fit.check(board, {"base": base, "lid": lid})
    assert names_failing(r) == [], fit.summary(r)
    h = board.holes[0]
    pillar = block(h.x + 1.4, h.y - 0.5, 3.0, h.x + 2.6, h.y + 0.5, t.z_top - 1)  # in the pillar's wall
    assert (lid & pillar).volume > 0.99 * pillar.volume
    head = block(h.x - 2, h.y - 2, t.z_bottom - 0.5, h.x + 2, h.y + 2, t.z_bottom + 2.5)  # the counterbore
    assert fit._overlap(base, head) < fit.EPS


def test_teardrop_has_a_point_on_top():
    from mech.box import teardrop
    for axis in ("x", "y"):
        d = teardrop(0, 0, 0, 4.0, 10, axis=axis)
        bb = d.bounding_box()
        assert abs(bb.max.Z - 2.0 * 2 ** 0.5) < 0.01 and abs(bb.min.Z + 2.0) < 0.01
        assert abs(d.volume - (3.14159 * 4 + 2.0 ** 2 - 3.14159 * 4 / 4) * 10) < 1.0


def test_fit_md_has_a_pass_or_fail_row_per_check(board):
    import copy
    b = copy.deepcopy(board)
    t = Tray(b)
    b.add_box("HS1", 33, 13, 10, 37, 17, 14, "heatsink over C1")
    r = fit.check(b, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    md = fit.markdown(r, "tiny")
    rows = [line.strip().strip("|").split("|") for line in md.splitlines() if line.startswith("| ")]
    got = {c[0].strip(): c[1].strip() for c in rows[1:]}
    assert got == {c["name"]: "pass" if c["ok"] else "fail" for c in r["checks"]}
    assert got["added_parts_clear"] == "fail" and all(len(c) == 3 for c in rows)
    assert any(n.startswith("hole_") for n in got) and "some fail" in md


def test_wall_tight_on_the_board_fails_board_gap(board):
    t = Tray(board)
    base = t.cut_ports(t.base())
    w = max(p[0] for p in board.outline)
    pad = block(w, 8, 0.0, w + 3, 12, board.thickness)  # touches the E edge: no overlap, no gap
    r = fit.check(board, {"base": base + pad, "lid": t.lid()})
    assert names_failing(r) == ["board_gap"], fit.summary(r)
    r = fit.check(board, {"base": base + pad, "lid": t.lid()}, min_gap=0.0)
    assert "board_gap" not in names_failing(r)


def test_boss_without_a_screw_hole_fails_screw(board):
    t = Tray(board)
    base = t.cut_ports(t.base())
    h = board.holes[0]
    r = fit.check(board, {"base": base + cylinder(h.x, h.y, -4, 0, 2.0), "lid": t.lid()})
    assert names_failing(r) == [f"screw_{h.ref}"], fit.summary(r)
    peg = cylinder(h.x, h.y, -4, board.thickness + 1, 2.0)  # a locating peg through the hole
    r = fit.check(board, {"base": base + peg, "lid": t.lid()})
    assert f"screw_{h.ref}" not in names_failing(r), fit.summary(r)


def test_only_runs_the_named_checks(board):
    t = Tray(board)
    r = fit.check(board, {"base": t.base(), "lid": t.lid()}, only={"port_J1", "lid_covers"})
    assert sorted(c["name"] for c in r["checks"]) == ["lid_covers", "port_J1"]
    assert names_failing(r) == ["port_J1"]


def test_screw_for_reads_the_drill():
    from mech.box import screw_for
    assert [screw_for(d) for d in (1.2, 2.2, 2.7, 3.2, 3.5, 4.3, 6.5)] == [1.6, 2.0, 2.5, 3.0, 3.0, 4.0, 6.0]


def test_pilot_follows_the_holes_screw(board):
    import copy
    from mech.fits import FITS
    b = copy.deepcopy(board)
    m2, m4 = b.holes[0], b.holes[1]
    m2.drill, m2.pad = 2.2, 0.0
    m4.drill, m4.pad = 4.3, 0.0
    t = Tray(b)
    assert t.pilot(m2) == round(FITS["pilot_d"] * 2 / 3, 2) and t.pilot(m4) == round(FITS["pilot_d"] * 4 / 3, 2)
    assert t.boss(m4) >= t.pilot(m4) + 4.5
    base = t.base()
    for h in (m2, m4):
        z = t.z_floor - 1.0
        hole = cylinder(h.x, h.y, z, z + 0.5, t.pilot(h) - 0.1)   # the pilot is open...
        rim = cylinder(h.x, h.y, z, z + 0.5, t.pilot(h) + 0.4) - cylinder(h.x, h.y, z - 1, z + 1, t.pilot(h) + 0.1)
        assert fit._overlap(base, hole) < fit.EPS
        assert (base & rim).volume > 0.99 * rim.volume              # ...and no wider
    for h in board.holes:                                            # an M3 board is unchanged
        assert screw_for_m3(h) and Tray(board).pilot(h) == FITS["pilot_d"]


def screw_for_m3(h):
    from mech.box import screw_for
    return screw_for(h.drill) == 3.0


def test_keepout_cone_through_the_lid(board):
    import copy
    b = copy.deepcopy(board)
    k = b.add_cone("c1_fov", ref="C1", half_angle=20)
    c1 = b.part("C1")
    assert k.apex[2] == pytest.approx(c1.z[1]) and k.axis == [0, 0, 1] and k.r0 > 0
    t = Tray(b)
    base, lid = t.cut_ports(t.base()), t.lid()
    r = fit.check(b, {"base": base, "lid": lid})
    assert names_failing(r) == ["keepout_c1_fov"]
    ko = next(c for c in r["checks"] if c["name"] == "keepout_c1_fov")
    assert list(ko["blocked_by"]) == ["lid"]
    # a clear window may sit in it; or the lid gets the cone cut through it
    b.keepouts[0].through = ["lid"]
    assert names_failing(fit.check(b, {"base": base, "lid": lid})) == []
    b.keepouts[0].through = []
    lid = t.clear_keepout(lid, "c1_fov")
    assert names_failing(fit.check(b, {"base": base, "lid": lid})) == []
    # a narrower hole than the cone fans out to is still in its way
    narrow = t.lid() - cylinder(c1.x, c1.y, t.z_top - 5, t.z_top + 5, 2 * k.r0)
    assert names_failing(fit.check(b, {"base": base, "lid": narrow})) == ["keepout_c1_fov"]


def test_keepout_side_cone_and_box_round_trip(board, tmp_path):
    import copy
    from mech.kicad import load
    b = copy.deepcopy(board)
    j1 = b.part("J1")
    b.add_cone("j1_out", ref="J1", axis="N", half_angle=5, length=10)
    assert b.keepouts[0].apex[1] == pytest.approx(j1.bbox[3])
    b.add_keepout_box("finger", 0, 0, 20, 5, 5, 30, note="reach")
    t = Tray(b)
    base = t.base()   # no port cut: the wall in front of J1 blocks its cone
    r = fit.check(b, {"base": base, "lid": t.lid()}, only={"keepout_j1_out", "keepout_finger"})
    assert names_failing(r) == ["keepout_j1_out"]
    p = tmp_path / "b.json"
    p.write_text(b.to_json())
    back = load(p)
    assert [k.name for k in back.keepouts] == ["j1_out", "finger"] and back.keepouts[1].box[2] == 20


def test_lid_lip_clears_a_wide_plugs_opening(board):
    """With low parts, the lid's lip comes down past a USB-C opening's top:
    the plug (12.4 wide) is wider than the receptacle the lip was notched
    over, so the lip is notched over the port's opening too."""
    import copy
    b = copy.deepcopy(board)
    for p in b.parts:
        if p.side == "top" and p.ref != "J1":
            p.z[1] = min(p.z[1], 4.0)
    t = Tray(b)
    _, _, _, _, z1, _, _ = t.opening(b.port("J1"))
    assert z1 > t.z_top - t.lip[0]           # the case this is about
    r = fit.check(b, {"base": t.cut_ports(t.base()), "lid": t.lid()})
    assert names_failing(r) == [], fit.summary(r)


def test_top_access_of_a_bottom_part_looks_down(board):
    """A bottom-side LED is seen through the floor: open below it, not above."""
    import copy
    from mech.kicad import Part
    b = copy.deepcopy(board)
    b.parts.append(Part("LED9", "led", "LED_0603", "bottom", 10.0, 10.0, 0.0, [9.0, 9.5, 11.0, 10.5],
                        [-0.8, 0.0], "table", False))
    b.top_access.append("LED9")
    t = Tray(b)
    base, lid = t.cut_ports(t.base()), t.lid()
    under = {x["ref"]: x["under"] for x in fit.check(b, {"base": base, "lid": lid})["top_access"]}
    assert under["LED9"] == ["base"]
    base -= cylinder(10.0, 10.0, t.z_bottom - 1, -0.5, 2.0)
    under = {x["ref"]: x["under"] for x in fit.check(b, {"base": base, "lid": lid})["top_access"]}
    assert under["LED9"] == []
