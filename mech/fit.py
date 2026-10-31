"""The fit check: does an enclosure, as assembled, hold this board?

    report = check(board, {"base": base, "lid": lid})
    report["ok"], report["checks"]

Each check is a name, ok, and what it measured; `ok` is all of them.

- board_clear: the board's outline slab touches no part of the enclosure.
- board_gap: the board drops in: nothing of the enclosure within min_gap
  (default 0.1 mm) of its outline's edge, through its thickness. A wall
  that only touches the board passes board_clear but won't take a printed
  part's tolerance.
- parts_clear: no part's box (courtyard x height, as mech.kicad read it),
  nor the box of a through-hole part's leads under the board, runs into the
  enclosure.
- added_parts_clear: what the script added to the board (Board.add_box: a
  heatsink, a binding post's nut, a second board) doesn't sit inside the
  board's own parts or their leads. Only when something was added.
- parts_apart: the enclosure's own parts (base, lid, buttons) don't run
  into each other as assembled.
- lid_covers: seen from above, the lid and the base's wall tops together
  cover the base's whole outer plan, so nothing is left open over the
  cavity but the holes cut into the lid. Only when there is a "base" and a
  "lid" (lid=, base= name others).
- port_<ref>: the opening the mating plug needs (the connector's face,
  grown to the plug's body for the port's kind: mech.kicad.PLUGS), carried
  straight out through the wall, is open; and the face sits no deeper
  behind the outside than the kind allows (a plug has to reach it).
  max_recess overrides that: a number for every port, or {ref: mm, "*": mm}.
  A round plug (SMA, BNC: kicad.ROUND) needs a round column, its plug's
  diameter across, so a round hole of that size passes.
- hole_<ref>: something solid holds the board under each mounting hole
  (an annulus round the hole, just below the board).
- screw_<ref>: something can go through each mounting hole: under the board
  its axis is open (a pilot or clearance hole for the screw, 2 mm down),
  or a peg fills the hole in the board. A boss left solid under the hole
  holds the board up but can't fix it.
- keepout_<name>: each keep-out volume on the board (Board.add_cone: a
  radar, IR or light sensor's field-of-view cone; Board.add_keepout_box)
  is empty of every enclosure part but the ones it lets through (a clear
  window). A cone with no length runs out past the enclosure.
- bed_<name>: each printed part fits the printer's bed (default 220 x 220 x
  250 mm), turned as `mech build` prints it.
- overhang_<name>, bridge_<name>, wall_<name>: each printed part, turned as
  it prints, prints without support: no surface steeper than 45 degrees
  from upright, every flat downward face held by wall (a bridge at most
  max_bridge across, a ledge at most a nozzle width out), no wall thinner
  than two nozzle widths (mech.printable). Only when the printed parts are
  given (`mech build`, `mech fit` and `mech score` give them); FIT's
  `printing` passes printable.check's keyword args (nozzle, max_bridge, ...).

It also lists, without judging, which top_access parts (switches, LEDs, IR
receivers) have an opening above them, and each part's volume and mass.
"""
from __future__ import annotations

import json
import math

from build123d import Axis, Compound, Cylinder, Face, Kind, Part, Polygon, Pos, Rot, extrude, offset

from .box import block, cylinder, keepout_solid
from .kicad import ADDED, ROUND, Board

EPS = 0.01  # mm^3: an overlap smaller than this is touching, not hitting
PETG = 1.27  # g/cm^3


def _vol(shape) -> float:
    try:
        return abs(shape.volume) if shape is not None else 0.0
    except Exception:
        return 0.0


def _overlap(a, b) -> float:
    try:
        return _vol(a & b)
    except Exception:
        return 0.0


def board_solid(board: Board) -> Part:
    return extrude(Polygon(*[tuple(p) for p in board.outline], align=None), amount=board.thickness)


def board_ring(board: Board, gap: float) -> Part:
    """The band gap wide round the board's outline, through its thickness."""
    face = Polygon(*[tuple(p) for p in board.outline], align=None)
    return extrude(offset(face, amount=gap, kind=Kind.INTERSECTION), amount=board.thickness) - board_solid(board)


def part_box(p) -> Part:
    return block(p.bbox[0], p.bbox[1], p.z[0], p.bbox[2], p.bbox[3], p.z[1])


def lead_box(p, size=(1e9, 1e9)) -> Part | None:
    """Where a through-hole part's leads stick out past the board's other
    face: its box, but only over the board (leads go through it), so a
    connector that overhangs the edge doesn't claim the wall under it."""
    x0, y0 = max(p.bbox[0], 0.0), max(p.bbox[1], 0.0)
    x1, y1 = min(p.bbox[2], size[0]), min(p.bbox[3], size[1])
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    if p.side == "top":
        return block(x0, y0, -p.under, x1, y1, 0.0)
    return block(x0, y0, p.z[1], x1, y1, p.z[1] + p.under)


def _box(p) -> tuple:
    return (p.bbox[0], p.bbox[1], p.z[0], p.bbox[2], p.bbox[3], p.z[1])


def _lead_span(p) -> tuple:
    """lead_box's corners, as numbers."""
    z = (-p.under, 0.0) if p.side == "top" else (p.z[1], p.z[1] + p.under)
    return (p.bbox[0], p.bbox[1], z[0], p.bbox[2], p.bbox[3], z[1])


def _boxes_meet(a, b, tol=0.01) -> bool:
    """Two boxes (x0, y0, z0, x1, y1, z1) overlap by more than tol on every axis."""
    return all(min(a[i + 3], b[i + 3]) - max(a[i], b[i]) > tol for i in range(3))


def _span(shape) -> tuple:
    """shape's bounding box as (x0, y0, z0, x1, y1, z1)."""
    bb = shape.bounding_box()
    return (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z)


def _section(part, z: float, fill: bool) -> list:
    """The faces where the plane at z cuts part, laid down at z = 0; fill: each with its holes filled in."""
    bb = part.bounding_box()
    slab = block(bb.min.X - 1, bb.min.Y - 1, z, bb.max.X + 1, bb.max.Y + 1, z + 0.02)
    try:
        cut = part & slab
    except Exception:
        return []
    out = []
    for f in cut.faces().filter_by(Axis.Z):
        if abs(f.center().Z - z) < 1e-3:
            out.append(Pos(0, 0, -z) * (Face(f.outer_wire()) if fill else f))
    return out


def _plan(part, levels: int = 8, fill: bool = True) -> list:
    """part seen from above, as faces: its sections at `levels` heights through it."""
    bb = part.bounding_box()
    z0, z1 = bb.min.Z + 0.05, bb.max.Z - 0.05
    if z1 <= z0:
        return []
    return [f for i in range(levels) for f in _section(part, z0 + (z1 - z0) * i / (levels - 1), fill)]


def _fuse(faces: list):
    if not faces:
        return None
    out = faces[0]
    for f in faces[1:]:
        out = out + f
    return out


def lid_gap(base, lid, sliver: float = 0.5) -> dict:
    """What of the base's outer plan neither the lid's outline nor the base's
    wall tops cover, from above. Slivers narrower than 2 x sliver (a lid
    dropped inside the walls with a fit gap) don't count."""
    need = _fuse(_plan(base))
    bb = base.bounding_box()
    have = _fuse(_plan(lid) + _section(base, bb.max.Z - 0.05, fill=False))
    if need is None:
        return {"open_mm2": 0.0, "where": []}
    rest = need - have if have is not None else need
    area, where = 0.0, []
    for f in rest.faces():
        try:
            core = offset(f, amount=-sliver, kind=Kind.INTERSECTION)
            if core is None or _vol_area(core) < 1e-3:
                continue
        except Exception:  # shrunk away to nothing
            continue
        area += f.area
        b = f.bounding_box()
        where.append([round(b.min.X, 1), round(b.min.Y, 1), round(b.max.X, 1), round(b.max.Y, 1)])
    return {"open_mm2": round(area, 1), "where": where}


def _vol_area(shape) -> float:
    try:
        return abs(shape.area)
    except Exception:
        return 0.0


def check(board: Board, parts: dict, bed=(220.0, 220.0, 250.0), max_recess: float | dict | None = None,
          printed: dict | None = None, lid: str = "lid", base: str = "base",
          printing: dict | None = None, min_gap: float = 0.1, only: set | None = None,
          skip=()) -> dict:
    """parts: name -> build123d Part, assembled. printed: name -> Part as printed (for the bed
    and printability checks). lid, base: which parts lid_covers weighs. printing:
    mech.printable.check's keyword args. min_gap: board_gap's clearance. only: run just
    the checks of these names (mech score's planted faults each need one). skip: checks
    not to run, by name or with a trailing * as a prefix ("hole_*": a board held by card
    guides, not its holes); FIT = {"skip": [...]} in a script."""
    checks = []

    def add(name, ok, **info):
        checks.append({"name": name, "ok": bool(ok), **info})

    def w(name) -> bool:
        if any(name == k or (k.endswith("*") and name.startswith(k[:-1])) for k in skip):
            return False
        return only is None or name in only
    enc = list(parts.values())
    whole = enc[0] if len(enc) == 1 else Compound(children=enc)
    outer = whole.bounding_box()

    if w("board_clear"):
        ov = sum(_overlap(board_solid(board), e) for e in enc)
        add("board_clear", ov < EPS, overlap_mm3=round(ov, 3))

    if w("board_gap"):
        edge = board_ring(board, min_gap)
        se = _span(edge)
        v = sum(_overlap(edge, e) for e in enc if _boxes_meet(se, _span(e), tol=0.0))
        add("board_gap", v < EPS, min_gap_mm=min_gap, within_mm3=round(v, 3))

    if lid in parts and base in parts and w("lid_covers"):
        g = lid_gap(parts[base], parts[lid])
        add("lid_covers", not g["where"], **g)

    clash = []
    names = list(parts) if w("parts_apart") else []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = _overlap(parts[a], parts[b])
            if v >= EPS:
                clash.append({"parts": [a, b], "overlap_mm3": round(v, 3)})
    if w("parts_apart"):
        add("parts_apart", not clash, clashes=clash)

    hits = []
    pc = w("parts_clear")
    boxes = [(p, part_box(p)) for p in board.parts if p.z[1] - p.z[0] > 0 and pc]
    leads = [(p, lead_box(p, board.size)) for p in board.parts if p.under > 0 and pc]
    boxes += [(p, b) for p, b in leads if b is not None]
    # One box against one part at a time, and only where their bounding boxes
    # meet: a Compound of all of them in one boolean took the core board's
    # 290 boxes to 6 GB and 5 minutes (issue #19); this is seconds and MBs.
    spans = [_span(e) for e in enc]
    for p, b in boxes:
        sb = _span(b)
        v = sum(_overlap(b, e) for e, se in zip(enc, spans) if _boxes_meet(sb, se, tol=0.0))
        if v >= EPS:
            hits.append({"ref": p.ref, "overlap_mm3": round(v, 3)})
    if pc:
        add("parts_clear", not hits, hits=hits)

    added = [p for p in board.parts if p.footprint.startswith(ADDED)] if w("added_parts_clear") else []
    if added:
        own = [(p.ref, _box(p)) for p in board.parts if not p.footprint.startswith(ADDED) and p.z[1] > p.z[0]]
        own += [(p.ref, _lead_span(p)) for p in board.parts if not p.footprint.startswith(ADDED) and p.under > 0]
        clash = sorted({(a.ref, ref) for a in added for ref, b in own if _boxes_meet(_box(a), b)})
        add("added_parts_clear", not clash, added=len(added), clashes=[list(c) for c in clash])

    for port in board.ports:
        if not w(f"port_{port.ref}"):
            continue
        u0, u1, z0, z1 = port.opening()
        lim = max_recess.get(port.ref, max_recess.get("*", port.max_recess())) if isinstance(max_recess, dict) \
            else (max_recess if max_recess is not None else port.max_recess())
        far = 1.0 + max(abs(outer.min.X), abs(outer.max.X), abs(outer.min.Y), abs(outer.max.Y))
        p = port.plane
        # The column straight out from the face, 0.05 mm in from each side.
        s = 0.05
        if port.kind in ROUND:   # the plug is round: a cylinder, so a round hole passes
            dia, cu, cz = min(u1 - u0, z1 - z0) - 2 * s, (u0 + u1) / 2, (z0 + z1) / 2
            ax = {"W": (-far, p - s), "E": (p + s, far), "S": (-far, p - s), "N": (p + s, far)}[port.edge]
            if port.edge in "WE":
                col = Pos((ax[0] + ax[1]) / 2, cu, cz) * Rot(0, 90, 0) * Cylinder(dia / 2, ax[1] - ax[0])
            else:
                col = Pos(cu, (ax[0] + ax[1]) / 2, cz) * Rot(90, 0, 0) * Cylinder(dia / 2, ax[1] - ax[0])
            recess = {"W": p - outer.min.X, "E": outer.max.X - p, "S": p - outer.min.Y, "N": outer.max.Y - p}[port.edge]
        elif port.edge == "W":
            col = block(-far, u0 + s, z0 + s, p - s, u1 - s, z1 - s)
            recess = p - outer.min.X
        elif port.edge == "E":
            col = block(p + s, u0 + s, z0 + s, far, u1 - s, z1 - s)
            recess = outer.max.X - p
        elif port.edge == "S":
            col = block(u0 + s, -far, z0 + s, u1 - s, p - s, z1 - s)
            recess = p - outer.min.Y
        else:
            col = block(u0 + s, p + s, z0 + s, u1 - s, far, z1 - s)
            recess = outer.max.Y - p
        v = sum(_overlap(col, e) for e in enc)
        add(f"port_{port.ref}", v < EPS and recess <= lim, edge=port.edge, kind=port.kind,
            blocked_mm3=round(v, 3), recess_mm=round(recess, 2), max_recess_mm=lim)

    for h in board.holes:
        if w(f"hole_{h.ref}"):
            r0, r1 = h.drill / 2 + 0.2, max(h.pad / 2, h.drill / 2 + 1.0)
            ring = cylinder(h.x, h.y, -0.3, 0.0, 2 * r1) - cylinder(h.x, h.y, -0.4, 0.1, 2 * r0)
            want = math.pi * (r1 * r1 - r0 * r0) * 0.3
            got = sum(_overlap(ring, e) for e in enc)
            add(f"hole_{h.ref}", got >= 0.8 * want, supported=round(got / want, 2) if want else 0)
        if not w(f"screw_{h.ref}"):
            continue
        d = min(1.0, 0.5 * h.drill)
        axis = sum(_overlap(cylinder(h.x, h.y, -2.0, -0.1, d), e) for e in enc)
        peg = sum(_overlap(cylinder(h.x, h.y, 0.1, board.thickness - 0.1, d), e) for e in enc)
        add(f"screw_{h.ref}", axis < EPS or peg >= EPS, axis_blocked_mm3=round(axis, 3), peg=peg >= EPS)

    for name, part in (printed or parts).items():
        if not w(f"bed_{name}"):
            continue
        bb = part.bounding_box()
        size = sorted([bb.size.X, bb.size.Y])
        fits = size[0] <= min(bed[:2]) and size[1] <= max(bed[:2]) and bb.size.Z <= bed[2]
        add(f"bed_{name}", fits, size_mm=[round(bb.size.X, 1), round(bb.size.Y, 1), round(bb.size.Z, 1)],
            bed_mm=list(bed))

    keepouts = getattr(board, "keepouts", None) or []
    reach = 2.0 + math.dist((outer.min.X, outer.min.Y, outer.min.Z), (outer.max.X, outer.max.Y, outer.max.Z))
    for k in keepouts:
        if not w(f"keepout_{k.name}"):
            continue
        vol = keepout_solid(k, reach)
        sk = _span(vol)
        hit = {}
        for n, e in parts.items():
            if n in k.through or not _boxes_meet(sk, _span(e), tol=0.0):
                continue
            v = _overlap(vol, e)
            if v >= EPS:
                hit[n] = round(v, 3)
        add(f"keepout_{k.name}", not hit, kind=k.kind, blocked_by=hit, through=list(k.through),
            **({"half_angle": k.half_angle} if k.kind == "cone" else {}))

    if printed:
        from . import printable
        for name, part in printed.items():
            if only is not None and not {f"{k}_{name}" for k in ("overhang", "bridge", "wall")} & only:
                continue
            for kind, r in printable.check(part, **(printing or {})).items():
                add(f"{kind}_{name}", r.pop("ok"), **r)

    top = []
    for ref in board.top_access if only is None else []:
        p = board.part(ref)
        cx, cy = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
        col = cylinder(cx, cy, p.z[1] + 0.01, outer.max.Z + 1, 1.0)
        over = [n for n, e in parts.items() if _overlap(col, e) >= EPS]
        top.append({"ref": ref, "open_above": not over, "under": over})

    mats = {}
    for name, part in parts.items():
        v = _vol(part) / 1000.0
        mats[name] = {"volume_cm3": round(v, 1), "solid_petg_g": round(v * PETG, 1)}
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "top_access": top, "material": mats}


def summary(report: dict) -> str:
    lines = []
    for c in report["checks"]:
        info = ", ".join(f"{k}={v}" for k, v in c.items() if k not in ("name", "ok"))
        lines.append(f"{'ok  ' if c['ok'] else 'FAIL'} {c['name']}: {info}")
    for t in report.get("top_access", []):
        lines.append(f"     top {t['ref']}: " + ('open above' if t['open_above'] else 'under ' + ', '.join(t['under'])))
    for n, m in report.get("material", {}).items():
        lines.append(f"     {n}: {m['volume_cm3']} cm3, {m['solid_petg_g']} g PETG if solid")
    passed = sum(c["ok"] for c in report["checks"])
    lines.append(f"fit: {passed}/{len(report['checks'])} checks pass")
    return "\n".join(lines)


def _cell(v) -> str:
    return (json.dumps(v) if isinstance(v, (list, dict)) else str(v)).replace("|", "\\|")


def markdown(report: dict, title: str = "") -> str:
    """The report as fit.md: a table with one row per check, its second cell
    pass or fail (what a project's own tests can read), then the parts
    reached from above and the material."""
    passed = sum(c["ok"] for c in report["checks"])
    lines = [f"# Fit{': ' + title if title else ''}", "",
             f"{passed}/{len(report['checks'])} checks pass" + ("." if report["ok"] else ", some fail."), "",
             "| check | result | measured |", "|---|---|---|"]
    for c in report["checks"]:
        info = ", ".join(f"{k}={_cell(v)}" for k, v in c.items() if k not in ("name", "ok"))
        lines.append(f"| {c['name']} | {'pass' if c['ok'] else 'fail'} | {info} |")
    if report.get("top_access"):
        lines += ["", "## Reached from above", ""]
        for t in report["top_access"]:
            lines.append(f"- {t['ref']}: " + ("open above" if t["open_above"] else "under " + ", ".join(t["under"])))
    if report.get("bought"):
        lines += ["", "## Bought", ""]
        lines += [f"- {n}" + (f": {w}" if w else "") for n, w in report["bought"].items()]
    if report.get("material"):
        lines += ["", "## Material", ""]
        for n, m in report["material"].items():
            if n in report.get("bought", {}):
                continue
            lines.append(f"- {n}: {m['volume_cm3']} cm3, {m['solid_petg_g']} g PETG if solid")
    return "\n".join(lines) + "\n"
