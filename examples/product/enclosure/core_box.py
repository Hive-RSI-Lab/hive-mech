"""The Plus and Ultra enclosures around the `core` board, in build123d with hive-mech.

`plus.py` and `ultra.py` are the scripts `mech build` runs; both call
build() here with their variant. `lite.py` uses the shared pieces too
(rca_ports, post_ports, base, lid, light_pipe, tray). Everything is in mech's board frame: mm,
x right along the core's front edge, y back (the rear panel is the N edge,
y = 100), z up, z = 0 the core's bottom face.

What mech's Tray gives and what this adds:

- **Tray:** the shell, the locating lip, port openings (USB-C, RJ45,
  TOSLINK), buttons through the lid and vents.
- **Here:** per-side gaps (the ESP32 antenna overhangs the E edge 1.5 mm,
  Ultra's front board stands in front of the S edge), screws from below
  into lid pillars (as on Lite), round RCA holes with one port per barrel,
  binding posts on the rear wall, heatsink room over each TPA3221, a long
  knob for the core's vertical EC11 (Plus) and Ultra's front panel around
  `ultra-front`.

The board is changed before mech's fit check sees it (`prepare`): parts
the variant doesn't fit are dropped, the RCA footprints are cut to their
body (their barrels become ports), internal connectors stop counting as
ports, and things that aren't on the board but must fit are added as
boxes: the heatsinks, the binding posts' nuts and lugs, and for Ultra the
front board, its parts and the LCD. mech then checks all of them against
the plastic.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from build123d import Box, Cylinder, Pos, Rectangle, Rot, Plane, extrude

from mech.box import Tray, block, cylinder, rounded
from mech.kicad import Board, Part, Port, load

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

WALL = 2.4          # 6 perimeters at 0.4
FLOOR = 2.0
LID = 2.2
STANDOFF = 5.0      # floor top to board bottom: the RCA, RJ45 and EC11 pins stick out 2.9-3.5 mm
GAP = {"W": 0.6, "S": 0.6, "E": 2.0, "N": 0.6}   # board edge to inner wall; E: the ESP32 antenna overhangs 1.5
TOP_CLEAR = 2.0     # RCA tops (28.6) to the lid's underside, Plus

SCREW_CLEAR = 3.4   # M3 through the floor, standoff and board
SCREW_HEAD = 6.2    # counterbore for an M3 socket or pan head
HEAD_DEPTH = 3.0
STANDOFF_OD = 7.0
RING = 0.8          # two 0.4 nozzle lines round the counterbore where it rises into the standoff: a foot
                    # SCREW_HEAD + 2 RING wide, to RING over the counterbore (under the boards' leads)
PILLAR_OD = 6.0
PILLAR_PILOT = 2.5  # M3 thread-forming into PETG
PILLAR_SHORT = 0.2  # pillar ends this far above the board, so the screw pulls the lid onto the walls

RCA_HOLE = 12.0     # passes a plug's outer shell onto the 8.3 mm barrel
RCA_CAP = 7.0       # its teardrop point is cut off 1 mm over the hole: a 3 mm bridge, and the upper one
                    # stays under Plus's wall top (30.6)
RCA_BARREL = 8.3
RCA_AXES = (6.5, 20.5)   # barrel axes above the board top (Lite's corrected model; same part)
POST_HOLE = 4.3     # M4-thread 4 mm binding posts
POST_NUT = 9.0      # M4 nut (8.1 across corners) + ring lug, inside the wall
POST_DEPTH = 10.0   # shank, nut and lug inside the wall
POST_X = (11.2, 30.2, 41.4, 60.4)  # 19 mm within a pair (dual banana plugs); between H1's pillar and J4
FOOT_D, FOOT_DEPTH = 11.0, 0.8

T_PCB = 1.6
RCA_TOP = T_PCB + 27.0


def dnp(variant: str) -> set[str]:
    f = {"plus": "parts.json", "ultra": "parts-ultra.json"}[variant]
    d = json.loads((ROOT / "boards/core/parts" / f).read_text())
    return {r for p in d["parts"] for r, c in p.get("refdes_class", {}).items() if c == "dnp"}


def box_part(ref, x0, y0, z0, x1, y1, z1, what="") -> Part:
    """Something that isn't a footprint on the core but must fit: a box mech's check knows."""
    return Part(ref, what, "enclosure:" + what, "top", (x0 + x1) / 2, (y0 + y1) / 2, 0.0,
                [x0, y0, x1, y1], [z0, z1], "override", False, None, 0.0)


def teardrop_y(x, y, z, d, length, cap=None) -> object:
    """A hole along y with a 45-degree point on top, so a vertical wall prints it without support.
    cap: cut the point off this far above the axis; the flat top is then a short bridge
    (2 * (r * sqrt(2) - cap) wide), for a big hole whose point would reach too high."""
    r = d / 2
    hole = Pos(x, y, z) * Rot(90, 0, 0) * Cylinder(r, length)
    tip = Pos(x, y, z + r / math.sqrt(2)) * Rot(0, 45, 0) * Box(r, length, r)
    if cap is not None:
        tip &= block(x - r, y - length, z, x + r, y + length, z + cap)
    return hole + tip


@dataclass
class CoreTray(Tray):
    """mech's Tray (box-shaped: its plan follows `inner`) with a gap per side and a fixed cavity top."""
    gaps: dict = None
    ztop: float = 30.6

    @property
    def inner(self):
        w, h = self.board.size
        g = self.gaps
        return (-g["W"], -g["S"], w + g["E"], h + g["N"])

    @property
    def z_top(self) -> float:
        return self.ztop


# --- shared with Lite (lite.py): RCA barrels and binding posts through the rear wall ---------

def rca_ports(board: Board, refs, feats: dict, gap_n: float):
    """Stacked RCA jacks on the N edge: the body ends inside the rear wall; each barrel is a port
    through a round hole (feats["rca"])."""
    for ref in refs:
        p = board.part(ref)
        p.bbox[3] = min(p.bbox[3], board.size[1] + gap_n - 0.05)
        for zb in RCA_AXES:
            z = T_PCB + zb
            h = RCA_BARREL / 2
            name = f"{ref}{'u' if zb > 10 else 'l'}"
            board.ports.append(Port(name, "N", [p.x - h, p.x + h, z - h, z + h], board.size[1], 0.0, "top"))
            feats.setdefault("rca", []).append((name, p.x, z))


def post_ports(board: Board, xs, zs: dict, y_wall_n: float, feats: dict):
    """Binding posts through the rear wall at each x, one row per zone at zs[zone] (above the board's
    bottom face): the hole is a port (a square inside the round hole), the nut and lug a box."""
    for zone, z in zs.items():
        for i, x in enumerate(xs):
            name = f"P{zone}{i + 1}"
            h = POST_HOLE / 2 / math.sqrt(2) - 0.1
            board.ports.append(Port(name, "N", [x - h, x + h, z - h, z + h], y_wall_n, 0.0, "top"))
            n = POST_NUT / 2
            board.parts.append(box_part(name, x - n, y_wall_n - POST_DEPTH, z - n, x + n, y_wall_n, z + n,
                                        "binding post nut and lug"))
            feats.setdefault("posts", []).append((name, x, z))


# --- the board as the box sees it ------------------------------------------------

def post_z(variant):
    """Binding-post axes above the board's bottom face: zone A, and zone B on Ultra."""
    return {"A": 21.0} if variant == "plus" else {"A": 19.0, "B": 33.0}


def prepare(board: Board, variant: str, y_wall_n: float) -> dict:
    """Change the board to what this enclosure holds; returns the features to cut."""
    gone = dnp(variant)
    board.parts = [p for p in board.parts if p.ref not in gone]
    board.top_access = [r for r in board.top_access if r not in gone]
    keep = {"J1", "J2"}          # USB-C and RJ45 on the W wall; the rest are internal or replaced
    board.ports = [p for p in board.ports if p.ref in keep]
    feats = {"rca": [], "posts": [], "heatsinks": []}

    rca_ports(board, ("J4", "J5"), feats, GAP["N"])

    # TOSLINK (Ultra): its body face, straight out of the rear wall.
    if "U19" not in gone:
        p = board.part("U19")
        board.ports.append(Port("U19", "N", [p.bbox[0], p.bbox[2], p.z[0], p.z[1]], p.bbox[3], 0.0, "top"))

    post_ports(board, POST_X, post_z(variant), y_wall_n, feats)

    # Heatsink room over each fitted TPA3221's top slug: the free area round it, up to the lid.
    room = {"U15": (2.0, 52.5, 21.0, 64.5), "U16": (35.4, 52.5, 53.4, 64.5)}
    for ref, (x0, y0, x1, y1) in room.items():
        if ref in gone:
            continue
        top = board.part(ref).z[1]
        feats["heatsinks"].append((ref, x0, y0, x1, y1, top))
    return feats


def add_heatsinks(board: Board, feats: dict, z_top: float):
    for ref, x0, y0, x1, y1, top in feats["heatsinks"]:
        board.parts.append(box_part(f"HS_{ref}", x0, y0, top, x1, y1, z_top - 1.0, "heatsink room"))


# --- the shell -----------------------------------------------------------------

def base(t: CoreTray, feats: dict, ports=("J1", "J2", "U19")):
    """Walls and floor, standoffs with the screws' clearance and counterbores from below, feet
    recesses, the `ports` mech found, and feats' RCA holes, post holes and heatsink floor vents."""
    x0, y0, x1, y1 = t.outer
    ix0, iy0, ix1, iy1 = t.inner
    b = rounded(x0, y0, x1, y1, t.z_bottom, t.z_top, t.corner + t.wall)
    b -= rounded(ix0, iy0, ix1, iy1, t.z_floor, t.z_top + 1, t.corner)
    for h in t.board.holes:
        b += cylinder(h.x, h.y, t.z_floor - 0.01, 0.0, max(STANDOFF_OD, h.pad))
        top = t.z_bottom + HEAD_DEPTH + RING
        r = SCREW_HEAD / 2 + RING
        b += cylinder(h.x, h.y, t.z_floor - 0.01, top, 2 * r)
        # a foot closer than 1 mm to a wall joins it, so no thin slot is left between them
        for near, rib in ((h.x - r - ix0, (ix0, h.y - r, h.x, h.y + r)),
                          (ix1 - h.x - r, (h.x, h.y - r, ix1, h.y + r)),
                          (h.y - r - iy0, (h.x - r, iy0, h.x + r, h.y)),
                          (iy1 - h.y - r, (h.x - r, h.y, h.x + r, iy1))):
            if near < 1.0:
                b += block(rib[0], rib[1], t.z_floor - 0.01, rib[2], rib[3], top)
        b -= cylinder(h.x, h.y, t.z_bottom - 1, 1.0, SCREW_CLEAR)
        b -= cylinder(h.x, h.y, t.z_bottom - 1, t.z_bottom + HEAD_DEPTH, SCREW_HEAD)
    # feet recesses
    for fx, fy in ((x0 + 9, y0 + 9), (x1 - 9, y0 + 9), (x0 + 9, y1 - 9), (x1 - 9, y1 - 9)):
        b -= cylinder(fx, fy, t.z_bottom - 1, t.z_bottom + FOOT_DEPTH, FOOT_D)
    b = t.cut_ports(b, refs=list(ports))
    cut = t.wall + 2
    yn = y1 - t.wall / 2
    for _, x, z in feats.get("rca", []):
        b -= teardrop_y(x, yn, z, RCA_HOLE, cut, cap=RCA_CAP)
    for _, x, z in feats.get("posts", []):
        b -= teardrop_y(x, yn, z, POST_HOLE, cut)
    for _, hx0, hy0, hx1, hy1, _ in feats.get("heatsinks", []):
        b = t.vents(b, (hx0 + 1, hy0 - 2, hx1 - 1, hy1 + 2), along="y", where="floor")
    return b


def lid(t: CoreTray, feats: dict):
    """mech's lid with a pillar onto each mounting hole (the screws from below thread into it)."""
    l = t.lid()
    for h in t.board.holes:
        l += cylinder(h.x, h.y, T_PCB + PILLAR_SHORT, t.z_top + 0.01, PILLAR_OD)
        l -= cylinder(h.x, h.y, T_PCB, t.z_top + LID - 0.8, PILLAR_PILOT)
    for _, hx0, hy0, hx1, hy1, _ in feats.get("heatsinks", []):
        l = t.vents(l, (hx0, hy0 - 2, hx1, hy1 + 2), along="y")
    return l


def button(t: CoreTray, lid_part, ref, d=4.6, rim=0.8, flange=1.5):
    """mech's Tray.button, made one solid: mech ends the cap `flange` under the lid and starts the
    flange 0.2 mm lower, so the flange (and the stem) would print floating over the cap."""
    lid_part, cap = t.button(lid_part, ref, d=d, rim=rim, flange=flange)
    p = t.board.part(ref)
    cx, cy = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
    cap += cylinder(cx, cy, t.z_top - flange - 0.3, t.z_top - flange + 0.1, d)
    return lid_part, cap


def tray(board: Board, variant: str, gaps: dict, z_top: float) -> CoreTray:
    return CoreTray(board, wall=WALL, floor=FLOOR, lid_t=LID, gap=GAP["W"], standoff=STANDOFF,
                    boss_d=STANDOFF_OD, gaps=gaps, ztop=z_top, shape="box")


# --- Plus: knob and buttons through the lid, IR and LED on the core ---------------

KNOB_D, KNOB_H, KNOB_LIFT = 25.0, 14.0, 1.0   # cap above the lid
KNOB_STEM, KNOB_HOLE = 11.0, 12.4
SHAFT_D, SHAFT_FLAT = 6.0, 4.5                 # EC11 D shaft: 6 mm, 4.5 across the flat
EC11_BUSHING = 11.5                            # bushing top above the board top (M7 bushing models)
EC11_SHAFT = (20.0, 24.5)                      # shaft tip above the board top: Alps 20 mm, library model 24.5


def d_bore(x, y, z0, z1, d=SHAFT_D + 0.2, flat=SHAFT_FLAT + 0.15, axis="z"):
    bore = cylinder(0, 0, z0, z1, d) & block(-d, -d, z0 - 1, d, -d / 2 + flat, z1 + 1)
    return Pos(x, y, 0) * bore


def plus_knob(t: CoreTray, lid_part, ref="SW1"):
    """A printed knob for the core's vertical EC11: a cap on the lid and a stem down to the shaft."""
    p = t.board.part(ref)
    x, y = p.x, p.y
    zl = t.z_top + LID
    stem_bot = T_PCB + EC11_BUSHING + 1.5          # 1.5 mm over the bushing, room for the push travel
    cap = cylinder(x, y, zl + KNOB_LIFT, zl + KNOB_LIFT + KNOB_H, KNOB_D)
    cap += cylinder(x, y, stem_bot, zl + KNOB_LIFT + 0.01, KNOB_STEM)
    # the bore takes a 20 mm shaft (Alps) through a 24.5 mm one (library model)
    cap -= Pos(x, y, 0) * (cylinder(0, 0, stem_bot - 1, T_PCB + EC11_SHAFT[1] + 0.6, SHAFT_D + 0.2)
                           & block(-4, -4, stem_bot - 2, 4, -(SHAFT_D + 0.2) / 2 + SHAFT_FLAT + 0.15, 60))
    lid_part -= cylinder(x, y, t.z_top - 5, zl + 1, KNOB_HOLE)
    return lid_part, cap


def light_pipe(t: CoreTray, lid_part, ref, od=3.6, idd=2.0, gap=0.6, dy=0.0):
    p = t.board.part(ref)
    cx, cy = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2 + dy
    lid_part += cylinder(cx, cy, p.z[1] + gap, t.z_top + 0.01, od)
    lid_part -= cylinder(cx, cy, p.z[1] + gap - 1, t.z_top + LID + 1, idd)
    return lid_part


def build_plus(board: Board) -> dict:
    z_top = RCA_TOP + TOP_CLEAR
    gaps = dict(GAP)
    y_wall_n = board.size[1] + gaps["N"]
    feats = prepare(board, "plus", y_wall_n)
    add_heatsinks(board, feats, z_top)
    # IR: a window in the front wall in front of the top-looking receiver, and one in the lid over it
    u = board.part("U18")
    board.ports.append(Port("U18", "S", [u.bbox[0], u.bbox[2], u.z[0], u.z[1]], u.bbox[1], 0.0, "top"))
    # SW1: the EC11's body, not its 24.5 mm courtyard column; the shaft runs up the knob's bore
    e = board.part("SW1")
    w, h, ht = EC11_BODY
    e.bbox, e.z = [e.x - w / 2, e.y - h / 2, e.x + w / 2, e.y + h / 2], [T_PCB, T_PCB + ht]
    board.parts.append(box_part("SW1_bushing", e.x - 3.5, e.y - 3.5, T_PCB + ht, e.x + 3.5, e.y + 3.5,
                                T_PCB + EC11_BUSHING, "EC11 M7 bushing"))
    t = tray(board, "plus", gaps, z_top)
    b = base(t, feats)
    b = t.cut_ports(b, refs=["U18"])
    l = lid(t, feats)
    l = t.cut_top(l, "U18", d=9.0)
    l, knob = plus_knob(t, l)
    l = light_pipe(t, l, "D3", dy=0.4)
    parts = {"base": b}
    for sw in ("SW2", "SW3"):
        l, btn = button(t, l, sw)
        parts[f"btn_{sw}"] = btn
    parts["knob"] = knob
    parts["lid"] = l
    return parts


# --- Ultra: the front board behind a front panel -----------------------------------

FRONT = HERE / "ultra-front.board.json"
FB_X = 6.0          # front board X = 0 at core x 6: its J1 (X 14) faces the core's J3 (x 20)
FB_ZB = -0.6        # front board's bottom edge, 4.4 mm over the floor for the FFC's turn
FB_BACK = -4.8      # its back face: 4.1 mm in front of the core's J3 mouth for the FFC's bend
FB_FACE = FB_BACK - T_PCB                    # its front (top) face, y
FB_WALL_IN = FB_FACE - 2.6                   # front wall's inner face: LCD 2.05 + tape 0.25 + 0.3
Z_TOP_ULTRA = 40.0  # front board top at 39.4
LCD = (25.45, 1.9, 77.25, 38.1, 2.05)        # module outline on the front board (mech frame) and thickness
LCD_ACTIVE = (40.8, 30.6)                    # centred in the module; datasheet p.10 offset unchecked, +1 mm each side
PLINTH = (89.8, 110.0)                       # front-board X of the thick right column: knob, IR, LED
POCKET = (91.8, 108.0, 0.6, 39.0, 7.2)       # X0 X1 Y0 Y1 and depth from the board face, for the EC11 body and IR
PLINTH_T = 2.0
FB_SCREW_PILOT, FB_BOSS = 2.1, 5.5           # M2.5 x 6 thread-forming, from behind the board
BTN_CAP, BTN_HOLE, BTN_FLANGE = 6.0, 6.6, 8.5
FKNOB_D, FKNOB_H, FKNOB_LIFT = 22.0, 15.0, 1.0
EC11_BODY = (12.4, 13.4, 6.6)                # w x h about the shaft, height above the board face


def fb_point(X, Y, zrel):
    """Front-board frame (mech: X right, Y up, zrel above its back face) to core frame."""
    return FB_X + X, FB_BACK - zrel, FB_ZB + Y


def fb_box(ref, X0, Y0, X1, Y1, z0, z1, what):
    """A box on the front board, z0..z1 above its back face, as a core-frame part."""
    return box_part(ref, FB_X + X0, FB_BACK - z1, FB_ZB + Y0, FB_X + X1, FB_BACK - z0, FB_ZB + Y1, what)


def front_parts(front: Board) -> list[Part]:
    out = [fb_box("FB", 0, 0, front.size[0], front.size[1], 0, T_PCB, "ultra-front board")]
    X0, Y0, X1, Y1, th = LCD
    out.append(fb_box("LCD", X0, Y0, X1, Y1, T_PCB, T_PCB + th + 0.25, "LCD module + tape"))
    for p in front.parts:
        if p.ref == "SW4":
            continue
        out.append(fb_box("F_" + p.ref, p.bbox[0], p.bbox[1], p.bbox[2], p.bbox[3], p.z[0], p.z[1], p.value))
        if p.under > 0:
            out.append(fb_box("F_" + p.ref + "_leads", p.bbox[0], p.bbox[1], p.bbox[2], p.bbox[3],
                              -p.under, 0.0, "leads"))
    e = front.part("SW4")
    w, h, ht = EC11_BODY
    out.append(fb_box("F_SW4", e.x - w / 2, e.y - h / 2, e.x + w / 2, e.y + h / 2, T_PCB, T_PCB + ht, "EC11 body"))
    out.append(fb_box("F_SW4_leads", e.bbox[0], e.bbox[1], e.bbox[2], e.bbox[3], -e.under, 0.0, "EC11 leads"))
    return out


def ultra_front_panel(t: CoreTray, b, front: Board):
    """The front wall's features for ultra-front, cut into (and added to) the base."""
    yi, yo = FB_WALL_IN, FB_WALL_IN - WALL
    zb, zt = t.z_bottom, t.z_top
    # the thick right column, and its pocket for the EC11 body and the IR receiver
    px0, px1 = FB_X + PLINTH[0], FB_X + PLINTH[1]
    kx0, kx1, kY0, kY1, kd = POCKET
    y_pocket = FB_FACE - kd
    b += block(px0, y_pocket - PLINTH_T, zb, px1, yo + 0.01, zt)
    b -= block(FB_X + kx0, y_pocket, FB_ZB + kY0, FB_X + kx1, yi + 0.01, FB_ZB + kY1)
    y_out_plinth = y_pocket - PLINTH_T
    # LCD window: active area + 1 mm a side at the inside, opening 45 degrees outward
    X0, Y0, X1, Y1, _ = LCD
    cx, cz = FB_X + (X0 + X1) / 2, FB_ZB + (Y0 + Y1) / 2
    aw, ah = LCD_ACTIVE[0] + 2, LCD_ACTIVE[1] + 2
    b -= Pos(cx, yi + 0.01, cz) * extrude(Plane.XZ * Rectangle(aw, ah), amount=WALL + 0.02, taper=-45)
    # three buttons: holes for the plungers' caps
    for ref in ("SW1", "SW2", "SW3"):
        p = front.part(ref)
        x, _, z = fb_point(p.x, p.y, 0)
        b -= Pos(x, yi, z) * Rot(90, 0, 0) * Cylinder(BTN_HOLE / 2, 2 * WALL + 2)
    # knob: a hole for the EC11's M7 bushing; IR window; LED hole
    e = front.part("SW4")
    x, _, z = fb_point(e.x, e.y, 0)
    b -= Pos(x, y_pocket, z) * Rot(90, 0, 0) * Cylinder(7.6 / 2, 2 * PLINTH_T + 2)
    u = front.part("U2")
    ux, _, uz = fb_point((u.bbox[0] + u.bbox[2]) / 2, (u.bbox[1] + u.bbox[3]) / 2, 0)
    b -= block(ux - 4.5, y_out_plinth - 1, uz - 3.0, ux + 4.5, y_pocket + 0.01, uz + 3.0)
    d = front.part("D1")
    dx, _, dz = fb_point((d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2, 0)
    b -= Pos(dx, y_pocket, dz) * Rot(90, 0, 0) * Cylinder(1.5, 2 * PLINTH_T + 2)
    # bosses for the front board's M2.5 screws, from the wall (or the pocket's floor) to its face
    for h in front.holes:
        x, _, z = fb_point(h.x, h.y, 0)
        in_pocket = kx0 - FB_BOSS / 2 < h.x < kx1 + FB_BOSS / 2
        y_from = y_pocket if in_pocket else yi
        boss = Pos(x, FB_FACE, z) * Rot(90, 0, 0) * Cylinder(FB_BOSS / 2, FB_FACE - y_from + 0.01,
                                                              align=(_C, _C, _MIN))
        pilot = Pos(x, FB_FACE + 0.01, z) * Rot(90, 0, 0) * Cylinder(FB_SCREW_PILOT / 2, 5.5,
                                                                    align=(_C, _C, _MIN))
        b = b + boss - pilot
    return b, y_out_plinth


def ultra_buttons(front: Board) -> dict:
    """Horizontal plungers for the front board's tact switches: a cap through the wall and a flange behind it."""
    out = {}
    yi = FB_WALL_IN
    for ref in ("SW1", "SW2", "SW3"):
        p = front.part(ref)
        x, _, z = fb_point(p.x, p.y, 0)
        act = FB_FACE - (p.z[1] - T_PCB)            # the actuator's front, y
        fl0, fl1 = yi + 0.05, act - 0.3
        # the cap runs 0.1 into the flange, so the button is one solid (its front stays at yi - WALL - 0.99)
        cap = Pos(x, fl0 + 0.1, z) * Rot(90, 0, 0) * Cylinder(BTN_CAP / 2, WALL + 1.14, align=(_C, _C, _MIN))
        cap += Pos(x, fl1, z) * Rot(90, 0, 0) * Cylinder(BTN_FLANGE / 2, fl1 - fl0, align=(_C, _C, _MIN))
        out[f"btn_F{ref}"] = cap
    return out


def ultra_knob(front: Board, y_out):
    e = front.part("SW4")
    x, _, z = fb_point(e.x, e.y, 0)
    y0 = y_out - FKNOB_LIFT
    k = Pos(x, y0, z) * Rot(90, 0, 0) * Cylinder(FKNOB_D / 2, FKNOB_H, align=(_C, _C, _MIN))
    tip = FB_FACE - EC11_SHAFT[1] - 0.6
    bore = Cylinder((SHAFT_D + 0.2) / 2, y0 - tip + 1, align=(_C, _C, _MIN)) & \
        Pos(0, -(SHAFT_D + 0.2) / 2 + SHAFT_FLAT + 0.15 - 4, 0) * Box(8, 8, 60)
    k -= Pos(x, y0 + 1, z) * Rot(90, 0, 0) * bore
    return k


def build_ultra(board: Board) -> dict:
    gaps = dict(GAP, S=-FB_WALL_IN)
    z_top = Z_TOP_ULTRA
    y_wall_n = board.size[1] + gaps["N"]
    feats = prepare(board, "ultra", y_wall_n)
    add_heatsinks(board, feats, z_top)
    front = load(FRONT)
    board.parts += front_parts(front)
    # the screen and the IR receiver are seen through the front panel: check them as ports
    X0, Y0, X1, Y1, th = LCD
    cx, cz = FB_X + (X0 + X1) / 2, FB_ZB + (Y0 + Y1) / 2
    aw, ah = LCD_ACTIVE
    board.ports.append(Port("LCD", "S", [cx - aw / 2, cx + aw / 2, cz - ah / 2, cz + ah / 2],
                            FB_FACE - th - 0.25, 0.0, "top"))
    u = front.part("U2")
    ux, _, uz = fb_point((u.bbox[0] + u.bbox[2]) / 2, (u.bbox[1] + u.bbox[3]) / 2, 0)
    board.ports.append(Port("F_U2", "S", [ux - 3.5, ux + 3.5, uz - 2.0, uz + 2.0],
                            FB_FACE - (u.z[1] - T_PCB), 0.0, "top"))
    t = tray(board, "ultra", gaps, z_top)
    b = base(t, feats)
    b, y_out = ultra_front_panel(t, b, front)
    l = lid(t, feats)
    # no lip along the front wall: the front board, its top bosses and the LCD stand there
    l -= block(t.inner[0] - 0.1, t.inner[1] - 0.1, z_top - t.lip[0] - 1, t.inner[2] + 0.1, FB_BACK + 0.5, z_top)
    l = light_pipe(t, l, "D3", dy=0.4)          # the core's status LED, seen from the top as on Plus
    parts = {"base": b}
    parts.update(ultra_buttons(front))
    parts["knob"] = ultra_knob(front, y_out)
    parts["lid"] = l
    return parts


from build123d import Align  # noqa: E402

_C, _MIN = Align.CENTER, Align.MIN
