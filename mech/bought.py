"""Bought enclosure parts as bricks: an extruded aluminium case with card
guides, and its flat end panels (aluminium, or a PCB used as a panel).

    from mech.bought import Hammond1455
    BOUGHT = {"shell": "Hammond 1455J1201", "front": "its end plate", "back": "its end plate"}
    def build(board):
        h = Hammond1455(board, "1455J1201", slot=1)
        front, back = h.end_plates()
        front = h.cut_ports(front)          # every W-edge port through the front plate
        back = h.cut_ports(back)
        return {"shell": h.shell(), "front": front, "back": back, "bezel_w": h.bezel("W"), ...}

A script names its bought parts in BOUGHT (a list, or name -> what it is):
`mech build` leaves them out of the STLs, the slicing and the printability
and bed checks, and keeps them in enclosure.step and every fit check, so a
bought case is checked against the board like a printed one.

Everything is in the board's frame (mech.kicad): the extrusion runs along x,
the board slides into its card guides along x, centred across y.

SERIES holds each case's numbers, from Hammond's drawing (cited per entry);
the profile is drawn from those numbers, not from Hammond's STEP (which is
403 to the Hive's sandbox): outside, wall, the card guides' slots and
pitch, the end plate, its screws and the bezel. Add a series from its
drawing the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from build123d import Part

from .box import block, rounded, teardrop
from .kicad import Board, Port

SERIES = {
    # hammfg.com/files/parts/pdf/1455J1201.pdf (fetched 2026-10-31): extrusion
    # 78.00 x 27.01 outside, 120.00 long, 1.50 wall; 75.00 between the slot
    # bottoms ("designed for 75 mm wide PC board"), slots 2.00 wide on a 3.50
    # pitch (1.50 ribs), 2.50 deep, four a side, the lowest 5.85 above the
    # inside floor (as hw-lab's vna enclosure read it); end plate 77.98 x
    # 26.92 x 1.50, four #6 screws on 69.98 x 18.92; bezel 78.13 x 30.13, 9.00
    # deep, 6.00 of it over the extrusion.
    "1455J1201": {
        "outside": (78.00, 27.01), "length": 120.00, "wall": 1.50, "corner": 3.5,
        "slot_span": 75.00, "slot_w": 2.00, "slot_depth": 2.50, "slot_z": (5.85, 9.35, 12.85, 16.35),
        "plate": (77.98, 26.92, 1.50), "screws": (69.98, 18.92, 3.5),
        "bezel": (78.13, 30.13, 9.00, 6.00),
    },
}


@dataclass
class Hammond1455:
    """A Hammond 1455 extruded case round `board`, the board in card-guide
    slot `slot` (0 the lowest). x0: where the extrusion starts along x
    (default: at the board's W edge when ports are on it, so they reach
    through the front plate, else centred). clear: the board's play in its slot."""
    board: Board
    series: str = "1455J1201"
    slot: int = 0
    x0: float | None = None
    clear: float = 0.2
    d: dict = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.series not in SERIES:
            raise ValueError(f"Hammond1455: no series {self.series!r} (have {', '.join(SERIES)})")
        self.d = SERIES[self.series]
        n = len(self.d["slot_z"])
        if not 0 <= self.slot < n:
            raise ValueError(f"Hammond1455 {self.series}: slot {self.slot}, want 0..{n - 1}")
        h, w = self.board.size   # x: along the case, y: across it
        span, depth = self.d["slot_span"], self.d["slot_depth"]
        if w > span - 2 * 0.1:
            raise ValueError(f"Hammond1455 {self.series}: the board is {w:g} mm across y, the slots "
                             f"{span:g} apart: at most {span - 0.2:g}")
        if w < span - 2 * depth + 2 * 1.0:
            raise ValueError(f"Hammond1455 {self.series}: the board is {w:g} mm across y; it needs "
                             f"{span - 2 * depth + 2:g} to sit 1 mm into each card guide")
        if h > self.d["length"]:
            raise ValueError(f"Hammond1455 {self.series}: the board is {h:g} mm long, the case {self.d['length']:g}")
        if self.x0 is None:
            # with ports on its W edge, the board's W edge 0.5 inside the extrusion's end
            # (clear of the plate), so they reach through the front plate; else centred
            w_ports = any(p.edge == "W" for p in self.board.ports)
            self.x0 = -0.5 if w_ports else (self.board.size[0] - self.d["length"]) / 2

    # The case's frame in the board's: x along the extrusion, y across, z up.
    @property
    def x1(self) -> float:
        return self.x0 + self.d["length"]

    @property
    def yc(self) -> float:
        return self.board.size[1] / 2

    @property
    def z_floor(self) -> float:
        """The inside floor: the board's bottom sits clear above the slot's lower face."""
        return -self.d["slot_z"][self.slot] - self.clear

    @property
    def z_mid(self) -> float:
        return self.z_floor - self.d["wall"] + self.d["outside"][1] / 2

    def shell(self) -> Part:
        """The extrusion: a rounded tube with four card-guide slots a side,
        each slot_depth into a rib that stands off the side wall."""
        d = self.d
        ow, oh = d["outside"]
        t, L = d["wall"], d["length"]
        y0, y1 = self.yc - ow / 2, self.yc + ow / 2
        zb = self.z_floor - t
        tube = rounded(self.x0, y0, self.x1, y1, zb, zb + oh, d["corner"])
        tube -= block(self.x0 - 1, y0 + t, zb + t, self.x1 + 1, y1 - t, zb + oh - t)
        # the guides: a comb on each inside wall, from the slot bottoms in by slot_depth
        sb0, sb1 = self.yc - d["slot_span"] / 2, self.yc + d["slot_span"] / 2
        zs = d["slot_z"]
        g0, g1 = zs[0] - 1.5, zs[-1] + d["slot_w"] + 1.5   # the comb: a rib below the lowest slot, one above the top
        for a, b in ((y0 + t - 0.01, sb0 + d["slot_depth"]), (sb1 - d["slot_depth"], y1 - t + 0.01)):
            comb = block(self.x0, a, self.z_floor + g0, self.x1, b, self.z_floor + g1)
            for z in zs:
                lo, hi = (sb0, b + 1) if a < self.yc else (a - 1, sb1)
                comb -= block(self.x0 - 1, lo, self.z_floor + z, self.x1 + 1, hi, self.z_floor + z + d["slot_w"])
            tube += comb
        return tube

    def end_plates(self, t: float | None = None) -> tuple[Part, Part]:
        """The two end plates (W at x0, E at x1), on the extrusion's ends,
        with their four screw holes. t: another thickness (a PCB panel)."""
        return self.end_plate("W", t), self.end_plate("E", t)

    def end_plate(self, edge: str, t: float | None = None) -> Part:
        pw, ph, pt = self.d["plate"]
        pt = t or pt
        sx, sz, sd = self.d["screws"]
        a, b = (self.x0 - pt, self.x0) if edge == "W" else (self.x1, self.x1 + pt)
        plate = block(a, self.yc - pw / 2, self.z_mid - ph / 2, b, self.yc + pw / 2, self.z_mid + ph / 2)
        for dy in (-sx / 2, sx / 2):
            for dz in (-sz / 2, sz / 2):
                plate -= teardrop(a + pt / 2, self.yc + dy, self.z_mid + dz, sd, pt + 2, axis="x")
        return plate

    def bezel(self, edge: str) -> Part:
        """A bezel round one end: a ring over the extrusion's last `over` mm,
        standing proud of it by the rest of its depth, open over the plate."""
        bw, bh, depth, over = self.d["bezel"]
        ow, oh = self.d["outside"]
        pw, ph, _ = self.d["plate"]
        if edge == "W":
            a, b = self.x0 - (depth - over), self.x0 + over
        else:
            a, b = self.x1 - over, self.x1 + (depth - over)
        ring = rounded(a, self.yc - bw / 2, b, self.yc + bw / 2, self.z_mid - bh / 2, self.z_mid + bh / 2, 4.0)
        zb = self.z_floor - self.d["wall"]
        ring -= rounded(min(a, b) - 1, self.yc - ow / 2, max(a, b) + 1, self.yc + ow / 2, zb, zb + oh, self.d["corner"])
        # the plate sits in it: open over the plate's face
        ring -= block(a - 1, self.yc - pw / 2 + 1.5, self.z_mid - ph / 2 + 1.5, b + 1,
                      self.yc + pw / 2 - 1.5, self.z_mid + ph / 2 - 1.5)
        return ring

    def cut_ports(self, plate: Part, edge: str | None = None, margin: float = 0.3) -> Part:
        """Each port on the plate's edge (W or E, read from the plate's x),
        cut through it: round for a round kind (SMA, BNC), else its plug's
        rectangle grown by margin."""
        if edge is None:
            bb = plate.bounding_box()
            edge = "W" if bb.max.X <= self.x0 + 0.01 else "E"
        bb = plate.bounding_box()
        a, b = bb.min.X - 1, bb.max.X + 1
        for p in self.board.ports:
            if p.edge != edge:
                continue
            plate = plate - port_hole(p, a, b, margin)
        return plate

    def parts(self, ports: bool = True) -> dict:
        """The whole case: shell, front (W) and back (E) plates with the
        ports cut, and both bezels."""
        w, e = self.end_plates()
        if ports:
            w, e = self.cut_ports(w, "W"), self.cut_ports(e, "E")
        return {"shell": self.shell(), "front": w, "back": e, "bezel_w": self.bezel("W"), "bezel_e": self.bezel("E")}


def port_hole(p: Port, a: float, b: float, margin: float = 0.3) -> Part:
    """The hole a port's mating plug needs through a panel or wall from a
    to b across it: a cylinder for a round kind (SMA, BNC: the plug's
    nut or sleeve), else the plug's rectangle (Port.opening)."""
    from .kicad import ROUND
    u0, u1, z0, z1 = p.opening()
    cu, cz = (u0 + u1) / 2, (z0 + z1) / 2
    if p.kind in ROUND:
        dia = max(u1 - u0, z1 - z0) + 2 * margin
        if p.edge in "WE":
            return teardrop((a + b) / 2, cu, cz, dia, b - a, axis="x")
        return teardrop(cu, (a + b) / 2, cz, dia, b - a, axis="y")
    u0, u1, z0, z1 = u0 - margin, u1 + margin, z0 - margin, z1 + margin
    return block(a, u0, z0, b, u1, z1) if p.edge in "WE" else block(u0, a, z0, u1, b, z1)


def panel(board: Board, edge: str, t: float = 1.6, margin: float = 3.0, at: float | None = None,
          z: tuple | None = None, ports: bool = True) -> Part:
    """A flat panel across one edge of the board (a front panel cut from
    aluminium or made as a PCB): the edge's length plus margin each side,
    from z[0] to z[1] (default: the board's lowest to its tallest part plus
    margin), its inner face at `at` (default: the edge's outermost port face,
    else the outline). Every port on that edge is cut through (port_hole)."""
    w, h = board.size
    on = [p for p in board.ports if p.edge == edge]
    if z is None:
        z = (-board.bottom_depth() - margin, board.top_height() + margin)
    if at is None:
        faces = [p.plane for p in on]
        at = {"W": min(faces + [0.0]), "E": max(faces + [w]), "S": min(faces + [0.0]), "N": max(faces + [h])}[edge]
    if edge == "W":
        a, b = at - t, at
        solid = block(a, -margin, z[0], b, h + margin, z[1])
    elif edge == "E":
        a, b = at, at + t
        solid = block(a, -margin, z[0], b, h + margin, z[1])
    elif edge == "S":
        a, b = at - t, at
        solid = block(-margin, a, z[0], w + margin, b, z[1])
    else:
        a, b = at, at + t
        solid = block(-margin, a, z[0], w + margin, b, z[1])
    if ports:
        for p in on:
            solid = solid - port_hole(p, a - 1, b + 1)
    return solid


def bought_names(mod) -> set:
    """The parts a script says it buys: BOUGHT as a list, set or dict."""
    b = getattr(mod, "BOUGHT", None) or ()
    return set(b.keys() if isinstance(b, dict) else b)

