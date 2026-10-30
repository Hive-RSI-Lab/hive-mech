"""Will a printed part print as it lies on the bed, without support?

    printable.check(part, nozzle=0.4, max_bridge=15.0, overhang=45.0)

part is turned as `mech build` prints it (mech.cli.printed_parts): the bed
at z = 0. It gives three checks, each a dict with ok and what it measured
(mech.fit names them overhang_<part>, bridge_<part> and wall_<part>):

- overhang: downward surface steeper than `overhang` degrees from upright
  (45 is the usual FDM limit; a teardrop's 45-degree point passes) that
  isn't flat. A horizontal round hole up to `hole_ok` mm across prints
  without support and doesn't count; the first layer doesn't either.
- bridge: every flat downward face above the bed (a port's top, a ledge, a
  flange's underside) is held by wall that goes down from its edges.
  Between two opposite edges it is a bridge, at most `max_bridge` mm
  across (PETG sags past about 15); held from one side only it is a ledge
  printed in air, at most `max_ledge` mm out (default 1);
  held from no side it starts in mid-air.
- wall: no wall, rib or tab, cut across at a height, is thinner than two
  nozzle widths (one perimeter each side): sections every `wall_step` mm,
  opened by a disc of radius nozzle; what the opening removes is too thin.
"""
from __future__ import annotations

import math

from build123d import Axis, Face, GeomType, Part, Vector

from .fits import FITS


FIRST_LAYER = 0.2  # mm: surface this close to the bed is the first layer's business


def _down_faces(part: Part, limit_z: float):
    """(face, steep area, flat) for each face with surface steeper than
    limit_z (a normal's z below it) above the first layer."""
    out = []
    for f in part.faces():
        bb = f.bounding_box()
        if bb.max.Z < FIRST_LAYER:
            continue
        if f.geom_type == GeomType.PLANE:
            nz = f.normal_at(f.center()).Z
            if nz < limit_z:
                out.append((f, f.area, nz < -0.985))
            continue
        try:
            verts, tris = f.tessellate(0.05, 0.3)
        except Exception:
            continue
        area = 0.0
        for tr in tris:
            a, b, c = (verts[i] for i in tr)
            n = (b - a).cross(c - a)
            m = (a + b + c) / 3
            if n.length < 1e-12 or m.Z < FIRST_LAYER:
                continue
            # The surface's own normal there, not the facet's: a 45-degree
            # cone's facets lean a little either way.
            try:
                nz = f.normal_at(m).Z
            except Exception:
                nz = n.Z / n.length
            if nz < limit_z:
                area += n.length / 2
        if area > 0:
            out.append((f, area, False))
    return out


def _edge_points(f: Face, step: float):
    """Points along f's edges, each with the in-plane direction pointing out of f."""
    pts = []
    for e in f.edges():
        n = max(2, int(math.ceil(e.length / step)) + 1)
        for i in range(n):
            t = (i + 0.5) / n
            p = e.position_at(t)
            tan = e.tangent_at(t)
            side = Vector(-tan.Y, tan.X, 0)
            if side.length < 1e-9:
                continue
            side = side.normalized()
            if f.is_inside(p + side * 0.05):
                side = -side
            pts.append((p, side))
    return pts


def _grid(f: Face, step: float):
    bb = f.bounding_box()
    nx = max(1, int(math.ceil((bb.max.X - bb.min.X) / step)))
    ny = max(1, int(math.ceil((bb.max.Y - bb.min.Y) / step)))
    z = (bb.min.Z + bb.max.Z) / 2
    pts = []
    for i in range(nx + 1):
        for j in range(ny + 1):
            p = Vector(bb.min.X + (bb.max.X - bb.min.X) * i / nx, bb.min.Y + (bb.max.Y - bb.min.Y) * j / ny, z)
            if f.is_inside(p, 1e-3):
                pts.append(p)
    return pts


def span(part: Part, f: Face, step: float = 0.5) -> dict:
    """How a flat downward face is held: its edges with wall going down from
    them are its anchors. For each point of the face, the nearest anchor and
    the nearest one the other way: with both it is a bridge (span = their
    sum), else a ledge (reach = the nearest). Returns the worst of each."""
    # Wall under an edge: solid just outside it and a little lower, also
    # under a printable slope (up to about 50 degrees from upright).
    anchors = [p for p, out in _edge_points(f, step / 2)
               if part.is_inside(p + out * 0.15 + Vector(0, 0, -0.12))]
    if not anchors:
        return {"anchored": False, "bridge": 0.0, "ledge": math.inf}
    bridge = ledge = 0.0
    for p in _grid(f, step) or [f.center()]:
        vs = [(a - p) for a in anchors]
        vs = [Vector(v.X, v.Y, 0) for v in vs]
        d1, v1 = min(((v.length, v) for v in vs), key=lambda t: t[0])
        if d1 < 1e-6:
            continue
        u1 = v1 / d1
        opp = [v.length for v in vs if v.length > 1e-6 and v.dot(u1) / v.length < -0.5]
        if opp:
            bridge = max(bridge, d1 + min(opp))
        else:
            ledge = max(ledge, d1)
    return {"anchored": True, "bridge": round(bridge, 2), "ledge": round(ledge, 2)}


def _where(f: Face) -> list:
    b = f.bounding_box()
    return [round(b.min.X, 1), round(b.min.Y, 1), round(b.min.Z, 1), round(b.max.X, 1), round(b.max.Y, 1)]


def _round_hole(f: Face, hole_ok: float) -> bool:
    """A horizontal cylindrical hole (or boss's underside round) small enough to print as is."""
    if f.geom_type != GeomType.CYLINDER:
        return False
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        cyl = BRepAdaptor_Surface(f.wrapped).Cylinder()
        axis = cyl.Axis().Direction()
        return abs(axis.Z()) < 0.1 and 2 * cyl.Radius() <= hole_ok
    except Exception:
        return False


def _poly(f: Face):
    """f (flat, at z = 0) as a shapely polygon, from its triangulation."""
    from shapely.geometry import Polygon as SPoly
    from shapely.ops import unary_union
    verts, tris = f.tessellate(0.02, 0.2)
    return unary_union([SPoly([(verts[i].X, verts[i].Y) for i in t]) for t in tris]).buffer(0)


def thin(part: Part, min_wall: float, step: float = 1.0, levels: int = 40) -> list:
    """Where sections of part across z are thinner than min_wall: [x0, y0, z, x1, y1, about how thick]."""
    from .fit import _section
    bb = part.bounding_box()
    z0, z1 = bb.min.Z + FIRST_LAYER, bb.max.Z - 0.1
    if z1 <= z0:
        return []
    n = min(levels, max(1, int((z1 - z0) / step)))
    zs = {round(z0 + (z1 - z0) * i / n, 3) for i in range(n + 1)}
    # Just above every flat face, where a thin tab or rim starts.
    for f in part.faces().filter_by(Axis.Z):
        z = f.center().Z + 0.1
        if z0 <= z <= z1:
            zs.add(round(z, 3))
    r = min_wall / 2 - 0.01
    rows, seen = [], set()
    for z in sorted(zs):
        for f in _section(part, z, fill=False):
            p = _poly(f)
            rest = p.difference(p.buffer(-r, join_style="mitre").buffer(r))
            for g in getattr(rest, "geoms", [rest]):
                if g.is_empty:
                    continue
                x0, y0, x1, y1 = g.bounds
                long = max(x1 - x0, y1 - y0)
                # A convex corner's rounding leaves a speck: only a strip counts.
                if long < 1.0 or g.area < 0.2:
                    continue
                k = (round(x0), round(y0), round(x1), round(y1))
                if k not in seen:  # one row per thin feature, not per height
                    seen.add(k)
                    rows.append([round(x0, 1), round(y0, 1), round(z, 1), round(x1, 1), round(y1, 1),
                                 round(2 * g.area / g.length, 2)])
    return rows


def check(part: Part, nozzle: float = 0.4, overhang: float = 45.0,
          max_bridge: float = FITS["max_bridge"], max_ledge: float = 1.0, hole_ok: float = 7.0,
          wall_step: float = 1.0) -> dict:
    """{"overhang": {...}, "bridge": {...}, "wall": {...}} for a part as printed."""
    limit_z = -math.sin(math.radians(overhang + 1.0))  # a degree's slack: teardrops are exactly 45
    steep, flats = [], []
    for f, area, flat in _down_faces(part, limit_z):
        if flat:
            flats.append(f)
        elif not _round_hole(f, hole_ok):
            steep.append({"at": _where(f), "mm2": round(area, 1)})
    steep_area = sum(s["mm2"] for s in steep)
    over = {"ok": steep_area < 1.0, "steep_mm2": round(steep_area, 1), "faces": steep[:8], "max_deg": overhang}

    bad, worst_bridge, worst_ledge = [], 0.0, 0.0
    for f in flats:
        s = span(part, f)
        worst_bridge = max(worst_bridge, s["bridge"])
        if s["anchored"]:
            worst_ledge = max(worst_ledge, s["ledge"])
        why = ("nothing under it" if not s["anchored"] else
               f"bridge {s['bridge']} mm" if s["bridge"] > max_bridge else
               f"ledge {s['ledge']} mm" if s["ledge"] > max_ledge + 0.05 else None)
        if why:
            bad.append({"at": _where(f), "why": why})
    bridge = {"ok": not bad, "flat_faces": len(flats), "worst_bridge_mm": worst_bridge,
              "worst_ledge_mm": worst_ledge, "max_bridge_mm": max_bridge, "max_ledge_mm": max_ledge,
              "unsupported": bad[:8]}

    rows = thin(part, 2 * nozzle, step=wall_step)
    wall = {"ok": not rows, "min_wall_mm": round(2 * nozzle, 2), "thin": rows[:8]}
    return {"overhang": over, "bridge": bridge, "wall": wall}
