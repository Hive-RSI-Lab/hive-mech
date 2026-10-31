"""Bricks for an enclosure around a board, in build123d.

Everything is in the board's frame (see mech.kicad): the board's bottom face
at z = 0, its outline box from (0, 0) to board.size. Build the parts where
they sit when assembled; `mech build` turns them for printing.

    from mech.box import Tray
    t = Tray(board, wall=2.0, standoff=5.0)
    base = t.base()          # floor, walls, bosses under the mounting holes
    base = t.cut_ports(base)  # every edge connector gets its opening
    lid = t.lid()            # a plate with a locating lip
    lid = t.cut_top(lid, "SW1", d=4)   # a hole over a part on the board
    lid = t.vents(lid, (60, 30, 95, 50))  # slots over a hot area
    base, lid = t.screws_from_below(base, lid)  # screws up through the board into lid pillars
    base -= teardrop(x, y, z, 4.3, 6)   # a hole through an upright wall that prints unsupported

Each brick is a plain function of numbers you can read and change: use the
ones that fit, write your own build123d for the rest. A Tray is only a
holder for the numbers they share.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import (Align, Box, Cone, Cylinder, Kind, Part, Plane, Polygon, Pos,
                       RectangleRounded, Rot, extrude, offset)

from .fits import FITS
from .kicad import Board, Port

MIN = (Align.MIN, Align.MIN, Align.MIN)

# Metric screw sizes a board's mounting hole is a clearance hole for.
SCREWS = (1.6, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0)


def screw_for(drill: float) -> float:
    """The metric screw a mounting hole of this drill takes: the largest
    size at least 0.1 under it (3.2 -> M3, 2.2 -> M2, 2.7 -> M2.5,
    4.3 -> M4). A hole too small for M1.6 gets M1.6."""
    fit = [m for m in SCREWS if m + 0.1 <= drill + 1e-9]
    return fit[-1] if fit else SCREWS[0]


def block(x0, y0, z0, x1, y1, z1) -> Part:
    """An axis-aligned box from corner to corner."""
    return Pos(x0, y0, z0) * Box(x1 - x0, y1 - y0, z1 - z0, align=MIN)


def rounded(x0, y0, x1, y1, z0, z1, r) -> Part:
    """A box from corner to corner with its vertical edges rounded by r."""
    w, h = x1 - x0, y1 - y0
    r = max(0.01, min(r, w / 2 - 0.01, h / 2 - 0.01))
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, z0) * extrude(RectangleRounded(w, h, r), amount=z1 - z0)


def cylinder(x, y, z0, z1, d) -> Part:
    return Pos(x, y, z0) * Cylinder(d / 2, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def teardrop(x, y, z, d, length, axis="y") -> Part:
    """A horizontal hole of diameter d along axis ("x" or "y"), centred at
    (x, y, z), with a 45-degree point on top, so a wall printed upright
    makes it without support (binding posts, a round jack)."""
    r = d / 2
    if axis == "y":
        hole = Pos(x, y, z) * Rot(90, 0, 0) * Cylinder(r, length)
        tip = Pos(x, y, z + r / math.sqrt(2)) * Rot(0, 45, 0) * Box(r, length, r)
    elif axis == "x":
        hole = Pos(x, y, z) * Rot(0, 90, 0) * Cylinder(r, length)
        tip = Pos(x, y, z + r / math.sqrt(2)) * Rot(45, 0, 0) * Box(length, r, r)
    else:
        raise ValueError(f"teardrop axis: {axis!r}, want 'x' or 'y'")
    return hole + tip


@dataclass
class Tray:
    """A two-part box: a base the board screws into, a lid that drops on.

    shape: "outline" (the cavity is the board's outline grown by gap, so a
    shaped board sits in a shaped box) or "box" (a rounded rectangle round
    `inner`, the outline's box grown by gap). The default is "outline",
    or "box" for a subclass that overrides `inner` (a gap per side, say):
    the outline's plan can't follow an overridden `inner`. gap: board edge to inner wall. corner: the box
    cavity's corner radius (default: the most that leaves half the gap at a
    square board corner; "outline" rounds each corner by gap instead). standoff: floor top to board bottom.
    top_clear: tallest top part to the lid's underside. boss_d, pilot_d: the
    standoff's outer diameter and screw hole (an M3 self-tapper's pilot,
    FITS["pilot_d"]; FITS["insert_d"] for an M3 heat-set insert). Both are
    the M3 sizes: a hole drilled for another screw (screw_for: 2.2 mm is
    M2, 4.3 is M4) gets them scaled to that screw (Tray.pilot), and a boss
    at least 4.5 mm wider than its pilot. The fits
    are mech/fits.json's, which the test-print coupon calibrates (mech.coupon). port_margin: added round each
    connector's face. lip: the lid's locating lip (height, thickness, fit gap).
    roof: the widest flat top an opening in a wall may have; a wider one gets
    45-degree shoulders, so the base prints upright without long bridges.
    web: the thinnest wall or lip left between two cuts; two openings closer
    than that become one, and so does a lip notch near the wall (two nozzle
    widths and a bit).
    """
    board: Board
    shape: str | None = None
    wall: float = 2.0
    floor: float = 2.0
    lid_t: float = 2.0
    gap: float = 0.6
    standoff: float = 5.0
    top_clear: float = 2.0
    corner: float | None = None
    boss_d: float = 7.0
    pilot_d: float = FITS["pilot_d"]
    port_margin: float = 0.5
    lip: tuple = (3.0, 1.2, FITS["lip_gap"])
    roof: float = 12.0
    web: float = 1.0
    ports: list[Port] = field(default=None)
    # What was cut so far: the base's openings and the lid's lip notches,
    # so a new one next to an old one merges with it (see web).
    _cuts: list = field(default_factory=list, init=False, repr=False)
    _notches: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        if self.corner is None:
            # The cavity's corner radius that still leaves half the gap at
            # a square board corner: r - sqrt(2) * (r - gap) >= gap / 2.
            # (3.4 x gap, the most that clears it at all, left 0.004 mm:
            # board_gap caught it.)
            self.corner = round(min(3.0, 2.2 * self.gap), 2)
        if self.ports is None:
            self.ports = list(self.board.ports)
        custom = type(self).inner is not Tray.inner and type(self).plan is Tray.plan
        if self.shape is None:
            self.shape = "box" if custom else "outline"
        elif self.shape == "outline" and custom:
            raise ValueError(f"{type(self).__name__} overrides inner, which shape='outline' can't follow: "
                             "use shape='box', or override plan() too")
        if self.shape not in ("outline", "box"):
            raise ValueError(f"Tray.shape: {self.shape!r}, want 'outline' or 'box'")

    def plan(self, d: float, z0: float, z1: float) -> Part:
        """The cavity's plan grown by d (negative: shrunk), extruded from z0 to z1.
        d = 0 is the cavity, d = wall the outside."""
        if self.shape == "box":
            x0, y0, x1, y1 = self.inner
            r = self.corner + d if d >= 0 else max(0.5, self.corner + d)
            return rounded(x0 - d, y0 - d, x1 + d, y1 + d, z0, z1, r)
        face = Polygon(*[tuple(p) for p in self.board.outline], align=None)
        g = self.gap + d
        if abs(g) > 1e-6:
            face = offset(face, amount=g, kind=Kind.ARC)
        return Pos(0, 0, z0) * extrude(face, amount=z1 - z0)

    # The cavity's and the outside's boxes, in the board's frame.
    @property
    def inner(self) -> tuple[float, float, float, float]:
        w, h = self.board.size
        g = self.gap
        return (-g, -g, w + g, h + g)

    @property
    def outer(self) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = self.inner
        t = self.wall
        return (x0 - t, y0 - t, x1 + t, y1 + t)

    @property
    def z_floor(self) -> float:
        """The floor's top face."""
        return -self.standoff

    @property
    def z_bottom(self) -> float:
        return -self.standoff - self.floor

    @property
    def z_top(self) -> float:
        """The cavity's top: where the base's walls end and the lid sits."""
        return self.board.top_height() + self.top_clear

    def base(self) -> Part:
        shell = self.plan(self.wall, self.z_bottom, self.z_top)
        shell -= self.plan(0.0, self.z_floor, self.z_top + 1)
        for h in self.board.holes:
            shell += cylinder(h.x, h.y, self.z_floor - 0.01, 0.0, self.boss(h))
        for h in self.board.holes:
            shell -= cylinder(h.x, h.y, self.z_floor - self.floor + 0.8, 0.01, self.pilot(h))
        return shell

    def scale(self, h) -> float:
        """The hole's screw over M3 (screw_for): what the M3 fits scale by."""
        return screw_for(h.drill) / 3.0

    def pilot(self, h) -> float:
        """The screw hole in the boss under mounting hole h: pilot_d for M3,
        scaled to the screw the hole is drilled for."""
        return round(self.pilot_d * self.scale(h), 2)

    def boss(self, h) -> float:
        """The boss's diameter under mounting hole h: boss_d, the pad, or
        4.5 mm more than the pilot, whichever is widest."""
        return max(self.boss_d, h.pad, self.pilot(h) + 4.5)

    def lid(self) -> Part:
        lh, lt, lg = self.lip
        plate = self.plan(self.wall, self.z_top, self.z_top + self.lid_t)
        ring = self.plan(-lg, self.z_top - lh, self.z_top)
        ring -= self.plan(-lg - lt, self.z_top - lh - 1, self.z_top + 1)
        lid = plate + ring
        self._notches = []
        # The lip mustn't land on a tall part or a connector near the wall.
        for p in self.board.parts:
            if p.side == "top" and p.z[1] > self.z_top - lh:
                lid = self.clear_lip(lid, p.bbox[0] - 0.5, p.bbox[1] - 0.5, p.bbox[2] + 0.5, p.bbox[3] + 0.5)
        return lid

    def clear_lip(self, lid: Part, x0: float, y0: float, x1: float, y1: float) -> Part:
        """Cut the lid's lip away over (x0, y0, x1, y1). Where that would
        leave less than `web` of lip against the wall, the cut runs on
        through the wall's line, and one within `web` of a notch already
        cut is cut as one with it: no sliver too thin to print is left."""
        lh, lt, lg = self.lip
        ix0, iy0, ix1, iy1 = self.inner
        near = lt + lg + self.web
        r = [ix0 - 1 if x0 - ix0 < near else x0, iy0 - 1 if y0 - iy0 < near else y0,
             ix1 + 1 if ix1 - x1 < near else x1, iy1 + 1 if iy1 - y1 < near else y1]
        merged = True
        while merged:
            merged = False
            for i, o in enumerate(self._notches):
                if max(r[0], o[0]) - min(r[2], o[2]) < self.web and max(r[1], o[1]) - min(r[3], o[3]) < self.web:
                    r = [min(r[0], o[0]), min(r[1], o[1]), max(r[2], o[2]), max(r[3], o[3])]
                    del self._notches[i]
                    merged = True
                    break
        self._notches.append(r)
        return lid - block(r[0], r[1], self.z_top - lh - 1, r[2], r[3], self.z_top)

    def screws_from_below(self, base: Part, lid: Part, clear: float = FITS["clear_d"], head: float = 6.2,
                          head_depth: float = 3.0, pillar_d: float = 6.0, pilot: float = FITS["pilot_d"],
                          short: float = 0.2) -> tuple[Part, Part]:
        """One screw per mounting hole, from under the base through the board
        into a pillar hanging from the lid: it clamps the board and holds the
        lid. Returns (base, lid). The base gets a clearance hole and a
        counterbore for the head; the lid a pillar that stops `short` above
        the board (so the screw pulls the lid down onto the walls) with a
        pilot for an M3 thread-former (2.5 in PETG). The sizes are M3's: a
        hole drilled for another screw (screw_for) gets clear, head and pilot
        scaled to it, and a pillar at least 3.5 mm wider than its pilot.
        Screw length: floor + standoff + board + about 6 mm of pillar."""
        zb, zt, top = self.z_bottom, self.z_top, self.board.thickness
        for h in self.board.holes:
            k = self.scale(h)
            base -= cylinder(h.x, h.y, zb - 1, 1.0, clear * k)
            base -= cylinder(h.x, h.y, zb - 1, zb + head_depth, head * k)
            lid += cylinder(h.x, h.y, top + short, zt + 0.01, max(pillar_d, pilot * k + 3.5))
            lid -= cylinder(h.x, h.y, top, zt + self.lid_t - 0.8, pilot * k)
        return base, lid

    def opening(self, port: Port, margin: float | None = None) -> tuple:
        """What port_box cuts, as numbers: (edge, u0, u1, z0, z1, a, b), the
        plug's outline grown by margin along the wall (u) and up (z), and
        a..b across the wall."""
        m = self.port_margin if margin is None else margin
        u0, u1, z0, z1 = port.opening()
        u0, u1, z0, z1 = u0 - m, u1 + m, z0 - m, z1 + m
        ox0, oy0, ox1, oy1 = self.outer
        ix0, iy0, ix1, iy1 = self.inner
        p = port.plane
        # From inside the cavity (or the face, if that is further in) out
        # past the wall: a connector that overhangs the board's edge passes
        # through the wall, not only its plug.
        if port.edge == "W":
            a, b = min(ox0, p) - 1, max(ix0, p) + 1
        elif port.edge == "E":
            a, b = min(ix1, p) - 1, max(ox1, p) + 1
        elif port.edge == "S":
            a, b = min(oy0, p) - 1, max(iy0, p) + 1
        else:
            a, b = min(iy1, p) - 1, max(oy1, p) + 1
        return (port.edge, u0, u1, z0, z1, a, b)

    def cut_box(self, edge: str, u0: float, u1: float, z0: float, z1: float, a: float, b: float) -> Part:
        """An opening's solid (see opening): a block, and over one wider than
        roof, 45-degree shoulders up to a flat top roof wide, so the wall
        above it prints upright without a long bridge."""
        cut = block(a, u0, z0, b, u1, z1) if edge in "WE" else block(u0, a, z0, u1, b, z1)
        if u1 - u0 <= self.roof:
            return cut
        h = (u1 - u0 - self.roof) / 2
        if edge in "WE":
            pl = Plane(origin=((a + b) / 2, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
        else:
            pl = Plane(origin=(0, (a + b) / 2, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        face = pl * Polygon((u0, z1 - 0.01), (u1, z1 - 0.01), (u1 - h, z1 + h), (u0 + h, z1 + h), align=None)
        return cut + extrude(face, amount=(b - a) / 2, both=True)

    def port_box(self, port: Port, margin: float | None = None) -> Part:
        """The solid a port's opening removes: the mating plug's outline
        (Port.opening) grown by margin, run from just inside the
        connector's face out past the wall, roofed (cut_box)."""
        return self.cut_box(*self.opening(port, margin))

    def cut_ports(self, part: Part, refs: list[str] | None = None, margin: float | None = None) -> Part:
        """Every port's opening (port_box), through open_wall."""
        for p in self.ports:
            if refs is None or p.ref in refs:
                part = self.open_wall(part, self.opening(p, margin))
        return part

    def open_wall(self, part: Part, cut: tuple) -> Part:
        """Cut an opening (see opening) out of part. One that would leave
        less than `web` of wall to an opening already cut on that wall is
        cut as one opening round both: a thinner web doesn't print."""
        merged = True
        while merged:
            merged = False
            for i, d in enumerate(self._cuts):
                gap = max(cut[1], d[1]) - min(cut[2], d[2])
                if cut[0] == d[0] and gap < self.web and min(cut[4], d[4]) > max(cut[3], d[3]):
                    cut = (cut[0], min(cut[1], d[1]), max(cut[2], d[2]), min(cut[3], d[3]), max(cut[4], d[4]),
                           min(cut[5], d[5]), max(cut[6], d[6]))
                    del self._cuts[i]
                    merged = True
                    break
        self._cuts.append(cut)
        return part - self.cut_box(*cut)

    def cut_top(self, lid: Part, ref: str, d: float | None = None, margin: float = 0.5) -> Part:
        """A hole through the lid over a part: round with diameter d, else its box grown by margin."""
        p = self.board.part(ref)
        z0, z1 = self.z_top - self.lip[0] - 1, self.z_top + self.lid_t + 1
        if d:
            return self.clear_lip(lid - cylinder(p.x, p.y, z0, z1, d), p.x - d / 2, p.y - d / 2, p.x + d / 2, p.y + d / 2)
        b = p.bbox
        x0, y0, x1, y1 = b[0] - margin, b[1] - margin, b[2] + margin, b[3] + margin
        return self.clear_lip(lid - block(x0, y0, z0, x1, y1, z1), x0, y0, x1, y1)

    def window(self, part: Part, ref: str, edge: str, margin: float = 0.5) -> Part:
        """An opening in the wall on `edge` (W E S N) in front of a part that
        isn't a connector: an IR receiver, an LED seen from the front."""
        p = self.board.part(ref)
        b = p.bbox
        port = Port(ref, edge, [b[1], b[3], p.z[0], p.z[1]] if edge in "WE" else [b[0], b[2], p.z[0], p.z[1]],
                    {"W": b[0], "E": b[2], "S": b[1], "N": b[3]}[edge], 0.0, p.side)
        return self.open_wall(part, self.opening(port, margin))

    def pocket(self, base: Part, ref: str, margin: float = 0.5, skin: float = 1.2) -> Part:
        """Room in the wall for a part that overhangs the board's edge (a
        module's antenna end, a connector body): its box grown by margin is
        cut out of the walls, and where that leaves less than `skin` of
        wall a pad is added outside it, the wall's full height. Inside the
        lid's outline the cut runs up to the wall's top (no ceiling to
        print in the air)."""
        p = self.board.part(ref)
        b = p.bbox
        x0, y0, x1, y1 = b[0] - margin, b[1] - margin, b[2] + margin, b[3] + margin
        z0, z1 = max(self.z_floor, p.z[0] - margin), min(self.z_top, p.z[1] + margin)
        ox0, oy0, ox1, oy1 = self.outer
        if ox0 <= x0 and oy0 <= y0 and x1 <= ox1 and y1 <= oy1:
            # Under the lid: open to the wall's top, so the pocket has no
            # ceiling to print in the air.
            z1 = self.z_top + 1
        base = base - block(x0, y0, z0, x1, y1, z1)
        ix0, iy0, ix1, iy1 = self.inner
        zb, zt = self.z_bottom, self.z_top
        if x1 + skin > ox1:
            base += rounded(ix1, y0 - skin, x1 + skin, y1 + skin, zb, zt, skin)
        if x0 - skin < ox0:
            base += rounded(x0 - skin, y0 - skin, ix0, y1 + skin, zb, zt, skin)
        if y1 + skin > oy1:
            base += rounded(x0 - skin, iy1, x1 + skin, y1 + skin, zb, zt, skin)
        if y0 - skin < oy0:
            base += rounded(x0 - skin, y0 - skin, x1 + skin, iy0, zb, zt, skin)
        return base - block(x0, y0, z0, x1, y1, z1)

    def button(self, lid: Part, ref: str, d: float = 5.0, clear: float = FITS["button_clear"],
               flange: float = 1.5, rim: float = 1.0, travel: float = 0.3) -> tuple[Part, Part]:
        """A printed push button for a top-actuated switch under the lid.

        Returns (lid with a hole, the button): a cap of diameter d through
        the lid, a flange under it that keeps it in, and a stem down to
        `travel` above the switch's top. Print the button on its cap."""
        p = self.board.part(ref)
        cx, cy = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
        zt = self.z_top
        lid = lid - cylinder(cx, cy, zt - 1, zt + self.lid_t + 1, d + 2 * clear)
        cap = cylinder(cx, cy, zt - flange, zt + self.lid_t + 1.0, d)
        under = zt - flange  # the flange, under the cap, below the lid's underside
        # Its face toward the cap is a 45-degree cone, so printed on the cap
        # the flange isn't a ledge in the air.
        cone = min(rim, 1.2)
        cap += cylinder(cx, cy, under - 1.2, under - cone, d + 2 * rim)
        cap += Pos(cx, cy, under - cone) * Cone(d / 2 + rim, d / 2, cone, align=(Align.CENTER, Align.CENTER, Align.MIN))
        # Room for the flange where the lid's lip would be.
        room = d / 2 + rim + clear
        lid = lid - cylinder(cx, cy, under - 1.2 - clear, zt, 2 * room)
        lid = self.clear_lip(lid, cx - room, cy - room, cx + room, cy + room)
        stem_top = under - 1.2
        stem_bot = p.z[1] + travel
        if stem_top > stem_bot:
            cap += cylinder(cx, cy, stem_bot, stem_top, min(3.0, d - 1))
        return lid, cap

    def vents(self, part: Part, area: tuple, slot: float = 2.0, pitch: float = 4.0, along: str = "x",
              where: str = "lid") -> Part:
        """Slots through the lid (or the floor) over area = (x0, y0, x1, y1) in the board's frame."""
        x0, y0, x1, y1 = area
        if where == "lid":
            z0, z1 = self.z_top - 0.01, self.z_top + self.lid_t + 1
        else:
            z0, z1 = self.z_bottom - 1, self.z_floor + 0.01
        cuts = None
        if along == "x":
            y = y0
            while y + slot <= y1 + 1e-6:
                c = rounded(x0, y, x1, y + slot, z0, z1, slot / 2 - 0.01)
                cuts = c if cuts is None else cuts + c
                y += pitch
        else:
            x = x0
            while x + slot <= x1 + 1e-6:
                c = rounded(x, y0, x + slot, y1, z0, z1, slot / 2 - 0.01)
                cuts = c if cuts is None else cuts + c
                x += pitch
        return part - cuts if cuts is not None else part


def flip(part: Part) -> Part:
    """Turned upside down about X, for printing a lid on its top face."""
    return Rot(180, 0, 0) * part
