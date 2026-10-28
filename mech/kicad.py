"""Pull what an enclosure needs out of a KiCad board file.

    board = read_board("boards/lite/kicad/lite-main.kicad_pcb")
    board.to_json()

What it reads, all from the .kicad_pcb itself (no KiCad needed):

- outline: the Edge.Cuts loops; the largest is the board, the rest cutouts.
- holes: mounting holes (MountingHole footprints, or an H/MH reference with
  a plated or bare hole of 2 mm or more), with drill and pad size.
- parts: every footprint's courtyard box on the board, its side, and its
  height: from its 3D model (STEP through OCP when the venv has it, VRML
  read directly), else an H<n> in its footprint or model name, else a table
  of common packages, else a guess (height_from says which). A heights file
  (read_heights) overrides any of them: models are often of another part.
- ports: connectors that sit at the board's edge (within edge_tol of the
  outline's box, or overhanging it), each with the edge it faces and the
  rectangle of its face on that edge.
- top_access: parts a person reaches or sees through the lid (switches,
  LEDs, IR receivers, displays, encoders); the enclosure decides which get a
  hole, light pipe or window.

The frame: millimetres, X right and Y up (KiCad's Y is flipped), origin at
the outline box's lower-left corner, z = 0 the board's bottom face. Top-side
parts span z from the board's thickness up; bottom-side parts go below 0.
`kicad_origin` maps back: kicad_x = x + ox, kicad_y = oy - y.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import sexpr
from .sexpr import find, find_all, head, value

EDGE_TOL = 2.0  # mm: a connector this close to the outline's box faces that edge

_CONN_REF = re.compile(r"^(J|P|CN|X|USB|XS|XP)\d", re.I)
_CONN_FP = re.compile(r"conn|usb|jack|rca|header|terminal|barrel|hdmi|rj45|dc[-_]?jack|socket|banana|xlr|audio", re.I)
_TOP_REF = re.compile(r"^(SW|S|BTN|LED|DS|ENC|RV|DISP|LCD|IR)\d", re.I)
_TOP_FP = re.compile(r"\bsw[-_]|switch|button|tact|led|encoder|irm|ir[-_]?recv|display|oled|lcd|pot", re.I)
_HOLE_FP = re.compile(r"mountinghole", re.I)
_HOLE_REF = re.compile(r"^(H|MH)\d", re.I)
_H_HINT = re.compile(r"(?:^|[-_])H(\d+(?:\.\d+)?)(?=[-_.]|$)")

# Heights in mm by footprint name, when no model or H<n> says.
_HEIGHTS = [
    (re.compile(r"0201|0402"), 0.5),
    (re.compile(r"0603|1608Metric"), 0.9),
    (re.compile(r"0805|2012Metric"), 1.3),
    (re.compile(r"1206|3216Metric|1210|3225Metric"), 1.7),
    (re.compile(r"SOT-?23|SOT-?323|SC-?70|TSOT"), 1.2),
    (re.compile(r"QFN|DFN|SON"), 1.0),
    (re.compile(r"SOIC|SOP|SSOP|TSSOP|MSOP"), 1.8),
    (re.compile(r"LQFP|TQFP|QFP"), 1.7),
    (re.compile(r"SMA|SMB|SMC|SOD"), 2.4),
    (re.compile(r"ESP32-WROVER|WROVER"), 3.3),
    (re.compile(r"ESP32-WROOM|WROOM"), 3.2),
]
_GUESS = 5.0

# What mates with a port from outside: (width, height) of the plug's body
# that must pass the wall, and how far behind the outside the port's face
# may sit for the plug to still seat (mm).
PLUGS = {
    "usb-c": (12.4, 6.6, 3.0),  # USB-IF's largest overmold, 12.35 x 6.5
    "usb-micro": (10.6, 7.0, 2.0),
    "usb-a": (16.0, 8.5, 2.0),
    "terminal": (0.0, 0.0, 6.0),  # wires go in at the face; screws from above
    "generic": (0.0, 0.0, 3.0),
}
_KINDS = [
    (re.compile(r"usb[-_]?c\b|type[-_]?c|usb4105|usb-3\.1-smd_16p", re.I), "usb-c"),
    (re.compile(r"micro[-_]?usb|usb[-_]?micro", re.I), "usb-micro"),
    (re.compile(r"usb[-_]?a\b", re.I), "usb-a"),
    (re.compile(r"terminal|screw|DB128|KF128|phoenix|5\.08|3\.81", re.I), "terminal"),
]
THT_LEAD = 2.5  # mm a through-hole lead sticks out when its model doesn't say


@dataclass
class Hole:
    ref: str
    x: float
    y: float
    drill: float
    pad: float
    plated: bool


@dataclass
class Part:
    ref: str
    value: str
    footprint: str
    side: str  # top | bottom
    x: float
    y: float
    rot: float
    bbox: list[float]  # x0, y0, x1, y1 on the board
    z: list[float]  # z0, z1
    height_from: str  # model | name | table | guess
    tht: bool
    model: str | None = None
    under: float = 0.0  # mm its leads stick out past the board's other face


@dataclass
class Port:
    ref: str
    edge: str  # W E S N: the side of the outline's box it faces
    face: list[float]  # u0, u1 along the edge (y for W/E, x for S/N), z0, z1
    plane: float  # x (W/E) or y (S/N) of the connector's outer face
    overhang: float  # mm the part sticks out past the outline's box (negative: set back)
    side: str
    kind: str = "generic"  # a key of PLUGS: what mates with it from outside

    def opening(self) -> list[float]:
        """u0, u1, z0, z1 the mating plug needs: the face, grown to the
        plug's size about the face's centre."""
        u0, u1, z0, z1 = self.face
        pw, ph, _ = PLUGS.get(self.kind, PLUGS["generic"])
        cu, cz = (u0 + u1) / 2, (z0 + z1) / 2
        w, h = max(u1 - u0, pw), max(z1 - z0, ph)
        return [cu - w / 2, cu + w / 2, cz - h / 2, cz + h / 2]

    def max_recess(self) -> float:
        return PLUGS.get(self.kind, PLUGS["generic"])[2]


@dataclass
class Board:
    source: str
    thickness: float
    outline: list[list[float]]
    cutouts: list[list[list[float]]]
    size: list[float]
    kicad_origin: list[float]
    holes: list[Hole] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    top_access: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def part(self, ref: str) -> Part:
        for p in self.parts:
            if p.ref == ref:
                return p
        raise KeyError(ref)

    def port(self, ref: str) -> Port:
        for p in self.ports:
            if p.ref == ref:
                return p
        raise KeyError(ref)

    def top_height(self) -> float:
        return max([p.z[1] for p in self.parts if p.side == "top"] + [self.thickness])

    def bottom_depth(self) -> float:
        """How far below z=0 the lowest bottom part or top part's lead goes, >= 0."""
        return max([-p.z[0] for p in self.parts if p.side == "bottom"]
                   + [p.under for p in self.parts if p.side == "top"] + [0.0])

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        d = dict(d)
        d["holes"] = [Hole(**h) for h in d.get("holes", [])]
        d["parts"] = [Part(**p) for p in d.get("parts", [])]
        d["ports"] = [Port(**p) for p in d.get("ports", [])]
        return cls(**d)


def load(path: str | os.PathLike) -> Board:
    """A board from a .kicad_pcb (read now) or a board.json (read back)."""
    p = Path(path)
    if p.suffix == ".json":
        return Board.from_dict(json.loads(p.read_text(encoding="utf-8")))
    return read_board(p)


# --- geometry ---------------------------------------------------------------

def _rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """KiCad's RotatePoint in its Y-down frame: positive degrees turn CCW on screen."""
    if not deg:
        return x, y
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


def _arc_points(start, mid, end, step_deg=10.0) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, end]
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1 = math.atan2(y1 - uy, x1 - ux)
    a2 = math.atan2(y2 - uy, x2 - ux)
    a3 = math.atan2(y3 - uy, x3 - ux)

    def ccw(a, b):  # sweep from a to b counter-clockwise, in [0, 2pi)
        return (b - a) % (2 * math.pi)
    sweep = ccw(a1, a3)
    if ccw(a1, a2) > sweep:  # mid isn't on the CCW path: go the other way
        sweep -= 2 * math.pi
    n = max(2, int(abs(math.degrees(sweep)) / step_deg) + 1)
    return [(ux + r * math.cos(a1 + sweep * i / n), uy + r * math.sin(a1 + sweep * i / n)) for i in range(n + 1)]


def _circle_points(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _area(poly) -> float:
    return 0.5 * sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))


def _xy(node, name):
    c = find(node, name)
    return (c[1], c[2]) if c is not None else None


def _edge_segments(root) -> tuple[list[list[tuple]], list[list[tuple]]]:
    """Edge.Cuts as open chains (lines, arcs) and closed loops (circles, rects, polys)."""
    chains, loops = [], []
    for g in root[1:]:
        h = head(g)
        if h not in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly", "gr_curve"):
            continue
        if value(g, "layer") != "Edge.Cuts":
            continue
        if h == "gr_line":
            chains.append([_xy(g, "start"), _xy(g, "end")])
        elif h == "gr_arc":
            chains.append(_arc_points(_xy(g, "start"), _xy(g, "mid"), _xy(g, "end")))
        elif h == "gr_curve":
            pts = [(p[1], p[2]) for p in find_all(find(g, "pts"), "xy")]
            chains.append([pts[0], pts[-1]])
        elif h == "gr_circle":
            c, e = _xy(g, "center"), _xy(g, "end")
            loops.append(_circle_points(c[0], c[1], math.hypot(e[0] - c[0], e[1] - c[1])))
        elif h == "gr_rect":
            (x0, y0), (x1, y1) = _xy(g, "start"), _xy(g, "end")
            loops.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        elif h == "gr_poly":
            loops.append([(p[1], p[2]) for p in find_all(find(g, "pts"), "xy")])
    return chains, loops


def _join(chains, tol=0.02) -> tuple[list[list[tuple]], int]:
    """Chains joined end to end into closed loops; also how many stayed open."""
    left = [list(c) for c in chains]
    loops, open_ = [], 0

    def near(a, b):
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
    while left:
        cur = left.pop(0)
        grown = True
        while grown and not near(cur[0], cur[-1]):
            grown = False
            for i, c in enumerate(left):
                if near(cur[-1], c[0]):
                    cur += c[1:]
                elif near(cur[-1], c[-1]):
                    cur += c[::-1][1:]
                elif near(cur[0], c[-1]):
                    cur = c[:-1] + cur
                elif near(cur[0], c[0]):
                    cur = c[::-1][:-1] + cur
                else:
                    continue
                left.pop(i)
                grown = True
                break
        if near(cur[0], cur[-1]) and len(cur) > 3:
            loops.append(cur[:-1])
        else:
            open_ += 1
    return loops, open_


# --- 3D models --------------------------------------------------------------

_model_cache: dict[str, tuple | None] = {}


def _resolve_model(path: str, pcb_dir: Path) -> Path | None:
    def sub(m):
        name = m.group(1) or m.group(2)
        if name == "KIPRJMOD":
            return str(pcb_dir)
        v = os.environ.get(name)
        if v:
            return v
        if re.match(r"KICAD\d*_3DMODEL_DIR", name):
            d = kicad_3dmodels()
            if d:
                return str(d)
        return m.group(0)
    p = Path(re.sub(r"\$\{(\w+)\}|\$\((\w+)\)", sub, path))
    if not p.is_absolute():
        p = pcb_dir / p
    # The file the footprint names first: its offset and rotation are for
    # that model (easyeda2kicad's STEP twins sit in another frame). Then the
    # other format, as KiCad swaps a missing .wrl for a .step and back.
    cands = [p, p.with_suffix(".wrl"), p.with_suffix(".step"), p.with_suffix(".stp")]
    for c in cands:
        if c.is_file():
            return c
    return None


def kicad_3dmodels() -> Path | None:
    """KiCad's stock 3D model folder: $KICAD_3DMODELS, else the newest KiCad in the Hive's tools."""
    env = os.environ.get("MECH_KICAD_3DMODELS")
    if env and Path(env).is_dir():
        return Path(env)
    tools = Path(os.environ.get("HIVE_TOOLS", "/hive/tools"))
    for d in sorted(tools.glob("kicad-*"), reverse=True):
        m = d / "share" / "kicad" / "3dmodels"
        if m.is_dir():
            return m
    return None


def model_bbox(path: Path) -> tuple | None:
    """(xmin, ymin, zmin, xmax, ymax, zmax) of a model in mm, in its own frame."""
    key = str(path)
    if key in _model_cache:
        return _model_cache[key]
    bb = None
    try:
        if path.suffix.lower() in (".step", ".stp"):
            bb = _step_bbox(path)
        elif path.suffix.lower() == ".wrl":
            bb = _wrl_bbox(path)
    except Exception:  # a model we can't read falls back to the name or table
        bb = None
    _model_cache[key] = bb
    return bb


def _step_bbox(path: Path):
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
    except ImportError:
        return None
    r = STEPControl_Reader()
    if r.ReadFile(str(path)) != IFSelect_RetDone:
        return None
    r.TransferRoots()
    shape = r.OneShape()
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    if box.IsVoid():
        return None
    lo, hi = box.CornerMin(), box.CornerMax()
    return (lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z())


_WRL_POINTS = re.compile(r"point\s*\[([^\]]*)\]", re.S)


def _wrl_bbox(path: Path):
    """VRML models are in KiCad's 0.1-inch units: 1 unit = 2.54 mm."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for block in _WRL_POINTS.findall(text):
        nums = [float(v) for v in re.split(r"[\s,]+", block.strip()) if v]
        for i in range(0, len(nums) - 2, 3):
            for k in range(3):
                v = nums[i + k]
                lo[k] = min(lo[k], v)
                hi[k] = max(hi[k], v)
    if lo[0] == math.inf:
        return None
    return tuple(v * 2.54 for v in lo) + tuple(v * 2.54 for v in hi)


def _rotate_xyz(p, rx, ry, rz):
    """KiCad places a model rotated by -rx, -ry, -rz about X, then Y, then Z."""
    x, y, z = p
    a = math.radians(-rx)
    y, z = y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a)
    a = math.radians(-ry)
    x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
    a = math.radians(-rz)
    x, y = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
    return x, y, z


def _model_z(fp, pcb_dir: Path) -> tuple[float, float, str] | None:
    """The z span of the footprint's first readable model above its side's surface."""
    for m in find_all(fp, "model"):
        if value(m, "hide") == "yes" or "hide" in m[1:]:
            continue
        path = _resolve_model(str(m[1]), pcb_dir)
        if not path:
            continue
        bb = model_bbox(path)
        if not bb:
            continue

        def xyz(name, default):
            c = find(m, name)
            c = find(c, "xyz") if c is not None else None
            return (c[1], c[2], c[3]) if c is not None else default
        sc, rt, of = xyz("scale", (1, 1, 1)), xyz("rotate", (0, 0, 0)), xyz("offset", (0, 0, 0))
        zs = []
        for cx in (bb[0], bb[3]):
            for cy in (bb[1], bb[4]):
                for cz in (bb[2], bb[5]):
                    zs.append(_rotate_xyz((cx * sc[0], cy * sc[1], cz * sc[2]), *rt)[2] + of[2])
        return min(zs), max(zs), path.name
    return None


def _table_height(name: str) -> tuple[float, str]:
    m = _H_HINT.search(name)
    if m:
        return float(m.group(1)), "name"
    for rx, h in _HEIGHTS:
        if rx.search(name):
            return h, "table"
    return _GUESS, "guess"


# --- the board ----------------------------------------------------------------

def read_heights(path: str | os.PathLike) -> dict:
    """A heights file: {key: {"above": mm, "below": mm, "note": ...}}, key a
    reference (J2) or a footprint name with or without its library
    (CONN-TH_XDJK-0271-1650). Keys starting with _ and "default" are skipped:
    a part no key names keeps what its model, name or the table says."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in d.items() if not k.startswith("_") and k != "default" and isinstance(v, dict)}


def read_board(path: str | os.PathLike, edge_tol: float = EDGE_TOL, heights: dict | None = None) -> Board:
    """heights: overrides from read_heights, which win over models: a
    datasheet's seated height beats a model drawn for another part."""
    path = Path(path)
    heights = heights or {}
    root = sexpr.parse(path.read_text(encoding="utf-8"))
    if head(root) != "kicad_pcb":
        raise ValueError(f"{path}: not a kicad_pcb file")
    warnings: list[str] = []
    thickness = value(find(root, "general") or ["general"], "thickness", 1.6)

    chains, loops = _edge_segments(root)
    joined, open_ = _join(chains)
    loops += joined
    if open_:
        warnings.append(f"Edge.Cuts: {open_} chain(s) don't close; the outline may be wrong")
    if not loops:
        raise ValueError(f"{path}: no closed outline on Edge.Cuts")
    loops.sort(key=lambda lp: abs(_area(lp)), reverse=True)
    allx = [p[0] for p in loops[0]]
    ally = [p[1] for p in loops[0]]
    ox, oy = min(allx), max(ally)  # KiCad's left and bottom (Y down)

    def tf(x, y):
        return [round(x - ox, 4), round(oy - y, 4)]

    def to_loop(lp):
        pts = [tf(*p) for p in lp]
        if _area(pts) < 0:
            pts.reverse()
        return pts
    outline = to_loop(loops[0])
    cutouts = [to_loop(lp) for lp in loops[1:]]
    size = [round(max(allx) - min(allx), 4), round(max(ally) - min(ally), 4)]

    holes, parts, ports, top_access = [], [], [], []
    pcb_dir = path.parent
    for fp in find_all(root, "footprint"):
        lib = str(fp[1])
        at = find(fp, "at")
        fx, fy, frot = at[1], at[2], (at[3] if len(at) > 3 and isinstance(at[3], float) else 0.0)
        side = "bottom" if value(fp, "layer") == "B.Cu" else "top"
        ref = val = ""
        for prop in find_all(fp, "property"):
            if prop[1] == "Reference":
                ref = str(prop[2])
            elif prop[1] == "Value":
                val = str(prop[2])
        if not ref:  # KiCad 6/7: (fp_text reference "R1")
            for t in find_all(fp, "fp_text"):
                if t[1] == "reference":
                    ref = str(t[2])
                elif t[1] == "value":
                    val = str(t[2])
        attr = find(fp, "attr")
        tht = attr is not None and "through_hole" in attr[1:]
        name = lib.split(":", 1)[-1]

        # Mounting holes.
        if _HOLE_FP.search(lib) or _HOLE_REF.match(ref):
            best = None
            for pad in find_all(fp, "pad"):
                dr = find(pad, "drill")
                if dr is None:
                    continue
                d = next((v for v in dr[1:] if isinstance(v, float)), 0.0)
                if d >= 2.0 and (best is None or d > best[0]):
                    pat = _xy(pad, "at") or (0.0, 0.0)
                    sz = find(pad, "size")
                    best = (d, pat, sz[1] if sz is not None else d, pad[2] == "thru_hole")
            if best:
                px, py = _rot(best[1][0], best[1][1], frot)
                hx, hy = tf(fx + px, fy + py)
                holes.append(Hole(ref, hx, hy, best[0], best[2], best[3]))
                continue

        # The courtyard (else fab, else pads) as the part's box.
        pts = []
        for layer_set in (("F.CrtYd", "B.CrtYd"), ("F.Fab", "B.Fab")):
            for g in fp[1:]:
                h = head(g)
                if h not in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
                    continue
                if value(g, "layer") not in layer_set:
                    continue
                if h in ("fp_line", "fp_rect", "fp_arc"):
                    for k in ("start", "end", "mid"):
                        if (q := _xy(g, k)):
                            pts.append(q)
                    if h == "fp_rect":
                        (x0, y0), (x1, y1) = _xy(g, "start"), _xy(g, "end")
                        pts += [(x0, y1), (x1, y0)]
                elif h == "fp_poly":
                    pts += [(q[1], q[2]) for q in find_all(find(g, "pts"), "xy")]
                elif h == "fp_circle":
                    c, e = _xy(g, "center"), _xy(g, "end")
                    r = math.hypot(e[0] - c[0], e[1] - c[1])
                    pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
            if pts:
                break
        if not pts:
            for pad in find_all(fp, "pad"):
                pa, sz = _xy(pad, "at"), find(pad, "size")
                if pa and sz is not None:
                    pts += [(pa[0] - sz[1] / 2, pa[1] - sz[2] / 2), (pa[0] + sz[1] / 2, pa[1] + sz[2] / 2)]
        if not pts:
            continue
        # A rect's corners can rotate off its box: take every corner of the local box.
        lx = [p[0] for p in pts]
        ly = [p[1] for p in pts]
        corners = [(a, b) for a in (min(lx), max(lx)) for b in (min(ly), max(ly))]
        board_pts = [tf(fx + rx, fy + ry) for rx, ry in (_rot(a, b, frot) for a, b in corners)]
        bx = [p[0] for p in board_pts]
        by = [p[1] for p in board_pts]
        bbox = [round(min(bx), 3), round(min(by), 3), round(max(bx), 3), round(max(by), 3)]

        mz = _model_z(fp, pcb_dir)
        model_name = None
        ov = heights.get(ref) or heights.get(lib) or heights.get(name)
        if ov:
            z0, z1 = 0.0, float(ov.get("above", 0.0))
            under = float(ov.get("below", 0.0))
            how = "override"
            model_name = mz[2] if mz else None
        elif mz:
            z0, z1, model_name = mz
            under = max(0.0, -z0 - thickness)
            z0, z1 = max(z0, 0.0), max(z1, 0.0)
            how = "model"
        else:
            h, how = _table_height(name)
            z0, z1 = 0.0, h
            under = THT_LEAD if tht else 0.0
            if how == "guess":
                warnings.append(f"{ref} ({name}): no model or known height; guessed {_GUESS} mm")
        if side == "top":
            z = [round(thickness + z0, 3), round(thickness + z1, 3)]
        else:
            z = [round(-z1, 3), round(-z0, 3)]
        if under > 3 * THT_LEAD and how != "override":  # a model whose origin isn't the board face: don't trust its leads
            under = THT_LEAD
        part = Part(ref, val, lib, side, *tf(fx, fy), frot, bbox, z, how, tht, model_name, round(under, 3))
        parts.append(part)

        is_conn = bool(_CONN_REF.match(ref) or _CONN_FP.search(name))
        if is_conn:
            gaps = {"W": bbox[0], "E": size[0] - bbox[2], "S": bbox[1], "N": size[1] - bbox[3]}
            edge = min(gaps, key=gaps.get)
            if gaps[edge] <= edge_tol:
                u0, u1 = (bbox[1], bbox[3]) if edge in "WE" else (bbox[0], bbox[2])
                plane = {"W": bbox[0], "E": bbox[2], "S": bbox[1], "N": bbox[3]}[edge]
                kind = next((k for rx, k in _KINDS if rx.search(name) or rx.search(val)), "generic")
                ports.append(Port(ref, edge, [u0, u1, z[0], z[1]], plane, round(-gaps[edge], 3), side, kind))
        if not is_conn and (_TOP_REF.match(ref) or _TOP_FP.search(name)) and not ref.upper().startswith(("R", "C", "L")):
            top_access.append(ref)

    return Board(str(path), thickness, outline, cutouts, size, [ox, oy], holes, parts, ports, top_access, warnings)
