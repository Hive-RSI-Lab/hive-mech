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

Each brick is a plain function of numbers you can read and change: use the
ones that fit, write your own build123d for the rest. A Tray is only a
holder for the numbers they share.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from build123d import (Align, Box, Cylinder, Part, Pos, RectangleRounded,
                       Rot, extrude)

from .kicad import Board, Port

MIN = (Align.MIN, Align.MIN, Align.MIN)


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


@dataclass
class Tray:
    """A two-part box: a base the board screws into, a lid that drops on.

    gap: board edge to inner wall. corner: the cavity's corner radius
    (default: the most that clears a square board corner). standoff: floor top to board bottom.
    top_clear: tallest top part to the lid's underside. boss_d, pilot_d: the
    standoff's outer diameter and screw hole (2.5 for an M3 self-tapper in
    PETG, 4.0 for an M3 heat-set insert). port_margin: added round each
    connector's face. lip: the lid's locating lip (height, thickness, fit gap).
    """
    board: Board
    wall: float = 2.0
    floor: float = 2.0
    lid_t: float = 2.0
    gap: float = 0.6
    standoff: float = 5.0
    top_clear: float = 2.0
    corner: float | None = None
    boss_d: float = 7.0
    pilot_d: float = 2.5
    port_margin: float = 0.5
    lip: tuple = (3.0, 1.2, 0.25)
    ports: list[Port] = field(default=None)

    def __post_init__(self):
        if self.corner is None:
            # The cavity's corner radius that still clears a square board
            # corner at `gap`: sqrt(2) * (r - gap) <= r.
            self.corner = round(min(3.0, 3.4 * self.gap), 2)
        if self.ports is None:
            self.ports = list(self.board.ports)

    # The cavity and the outside, in the board's frame.
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
        x0, y0, x1, y1 = self.outer
        ix0, iy0, ix1, iy1 = self.inner
        shell = rounded(x0, y0, x1, y1, self.z_bottom, self.z_top, self.corner + self.wall)
        shell -= rounded(ix0, iy0, ix1, iy1, self.z_floor, self.z_top + 1, self.corner)
        for h in self.board.holes:
            shell += cylinder(h.x, h.y, self.z_floor - 0.01, 0.0, max(self.boss_d, h.pad))
        for h in self.board.holes:
            shell -= cylinder(h.x, h.y, self.z_floor - self.floor + 0.8, 0.01, self.pilot_d)
        return shell

    def lid(self) -> Part:
        x0, y0, x1, y1 = self.outer
        ix0, iy0, ix1, iy1 = self.inner
        lh, lt, lg = self.lip
        plate = rounded(x0, y0, x1, y1, self.z_top, self.z_top + self.lid_t, self.corner + self.wall)
        ring = rounded(ix0 + lg, iy0 + lg, ix1 - lg, iy1 - lg, self.z_top - lh, self.z_top, max(0.5, self.corner - lg))
        ring -= rounded(ix0 + lg + lt, iy0 + lg + lt, ix1 - lg - lt, iy1 - lg - lt, self.z_top - lh - 1,
                        self.z_top + 1, max(0.5, self.corner - lg - lt))
        lid = plate + ring
        # The lip mustn't land on a tall part or a connector near the wall.
        for p in self.board.parts:
            if p.side == "top" and p.z[1] > self.z_top - lh:
                lid -= block(p.bbox[0] - 0.5, p.bbox[1] - 0.5, self.z_top - lh - 1,
                             p.bbox[2] + 0.5, p.bbox[3] + 0.5, self.z_top)
        return lid

    def port_box(self, port: Port, margin: float | None = None) -> Part:
        """The solid a port's opening removes: the mating plug's outline
        (Port.opening) grown by margin, run from just inside the
        connector's face out past the wall."""
        m = self.port_margin if margin is None else margin
        u0, u1, z0, z1 = port.opening()
        u0, u1, z0, z1 = u0 - m, u1 + m, z0 - m, z1 + m
        ox0, oy0, ox1, oy1 = self.outer
        p = port.plane
        if port.edge == "W":
            return block(min(ox0, p) - 1, u0, z0, p + 1, u1, z1)
        if port.edge == "E":
            return block(p - 1, u0, z0, max(ox1, p) + 1, u1, z1)
        if port.edge == "S":
            return block(u0, min(oy0, p) - 1, z0, u1, p + 1, z1)
        return block(u0, p - 1, z0, u1, max(oy1, p) + 1, z1)

    def cut_ports(self, part: Part, refs: list[str] | None = None, margin: float | None = None) -> Part:
        for p in self.ports:
            if refs is None or p.ref in refs:
                part -= self.port_box(p, margin)
        return part

    def cut_top(self, lid: Part, ref: str, d: float | None = None, margin: float = 0.5) -> Part:
        """A hole through the lid over a part: round with diameter d, else its box grown by margin."""
        p = self.board.part(ref)
        z0, z1 = self.z_top - self.lip[0] - 1, self.z_top + self.lid_t + 1
        if d:
            return lid - cylinder(p.x, p.y, z0, z1, d)
        b = p.bbox
        return lid - block(b[0] - margin, b[1] - margin, z0, b[2] + margin, b[3] + margin, z1)

    def window(self, part: Part, ref: str, edge: str, margin: float = 0.5) -> Part:
        """An opening in the wall on `edge` (W E S N) in front of a part that
        isn't a connector: an IR receiver, an LED seen from the front."""
        p = self.board.part(ref)
        b = p.bbox
        port = Port(ref, edge, [b[1], b[3], p.z[0], p.z[1]] if edge in "WE" else [b[0], b[2], p.z[0], p.z[1]],
                    {"W": b[0], "E": b[2], "S": b[1], "N": b[3]}[edge], 0.0, p.side)
        return part - self.port_box(port, margin)

    def button(self, lid: Part, ref: str, d: float = 5.0, clear: float = 0.3, flange: float = 1.5,
               rim: float = 1.0, travel: float = 0.3) -> tuple[Part, Part]:
        """A printed push button for a top-actuated switch under the lid.

        Returns (lid with a hole, the button): a cap of diameter d through
        the lid, a flange under it that keeps it in, and a stem down to
        `travel` above the switch's top. Print the button on its cap."""
        p = self.board.part(ref)
        cx, cy = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
        zt = self.z_top
        lid = lid - cylinder(cx, cy, zt - 1, zt + self.lid_t + 1, d + 2 * clear)
        cap = cylinder(cx, cy, zt - flange, zt + self.lid_t + 1.0, d)
        under = zt - flange - 0.2  # the flange, just below the lid's underside
        cap += cylinder(cx, cy, under - 1.2, under, d + 2 * rim)
        # Room for the flange where the lid's lip would be.
        lid = lid - cylinder(cx, cy, under - 1.2 - clear, zt, d + 2 * rim + 2 * clear)
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
