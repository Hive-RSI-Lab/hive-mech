"""The fit check: does an enclosure, as assembled, hold this board?

    report = check(board, {"base": base, "lid": lid})
    report["ok"], report["checks"]

Each check is a name, ok, and what it measured; `ok` is all of them.

- board_clear: the board's outline slab touches no part of the enclosure.
- parts_clear: no part's box (courtyard x height, as mech.kicad read it),
  nor the box of a through-hole part's leads under the board, runs into the
  enclosure.
- parts_apart: the enclosure's own parts (base, lid, buttons) don't run
  into each other as assembled.
- port_<ref>: the opening the mating plug needs (the connector's face,
  grown to the plug's body for the port's kind: mech.kicad.PLUGS), carried
  straight out through the wall, is open; and the face sits no deeper
  behind the outside than the kind allows (a plug has to reach it).
  max_recess overrides that: a number for every port, or {ref: mm, "*": mm}.
- hole_<ref>: something solid holds the board under each mounting hole
  (an annulus round the hole, just below the board).
- bed_<name>: each printed part fits the printer's bed (default 220 x 220 x
  250 mm), turned as `mech build` prints it.

It also lists, without judging, which top_access parts (switches, LEDs, IR
receivers) have an opening above them, and each part's volume and mass.
"""
from __future__ import annotations

import math

from build123d import Compound, Part, Polygon, extrude

from .box import block, cylinder
from .kicad import Board

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


def check(board: Board, parts: dict, bed=(220.0, 220.0, 250.0), max_recess: float | dict | None = None,
          printed: dict | None = None) -> dict:
    """parts: name -> build123d Part, assembled. printed: name -> Part as printed (for the bed check)."""
    checks = []

    def add(name, ok, **info):
        checks.append({"name": name, "ok": bool(ok), **info})
    enc = list(parts.values())
    whole = enc[0] if len(enc) == 1 else Compound(children=enc)
    outer = whole.bounding_box()

    ov = sum(_overlap(board_solid(board), e) for e in enc)
    add("board_clear", ov < EPS, overlap_mm3=round(ov, 3))

    clash = []
    names = list(parts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = _overlap(parts[a], parts[b])
            if v >= EPS:
                clash.append({"parts": [a, b], "overlap_mm3": round(v, 3)})
    add("parts_apart", not clash, clashes=clash)

    hits = []
    boxes = [(p, part_box(p)) for p in board.parts if p.z[1] - p.z[0] > 0]
    leads = [(p, lead_box(p, board.size)) for p in board.parts if p.under > 0]
    boxes += [(p, b) for p, b in leads if b is not None]
    allboxes = Compound(children=[b for _, b in boxes]) if boxes else None
    if allboxes is not None and any(_overlap(allboxes, e) >= EPS for e in enc):
        for p, b in boxes:
            v = sum(_overlap(b, e) for e in enc)
            if v >= EPS:
                hits.append({"ref": p.ref, "overlap_mm3": round(v, 3)})
    add("parts_clear", not hits, hits=hits)

    for port in board.ports:
        u0, u1, z0, z1 = port.opening()
        lim = max_recess.get(port.ref, max_recess.get("*", port.max_recess())) if isinstance(max_recess, dict) \
            else (max_recess if max_recess is not None else port.max_recess())
        far = 1.0 + max(abs(outer.min.X), abs(outer.max.X), abs(outer.min.Y), abs(outer.max.Y))
        p = port.plane
        # The column straight out from the face, 0.05 mm in from each side.
        s = 0.05
        if port.edge == "W":
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
        r0, r1 = h.drill / 2 + 0.2, max(h.pad / 2, h.drill / 2 + 1.0)
        ring = cylinder(h.x, h.y, -0.3, 0.0, 2 * r1) - cylinder(h.x, h.y, -0.4, 0.1, 2 * r0)
        want = math.pi * (r1 * r1 - r0 * r0) * 0.3
        got = sum(_overlap(ring, e) for e in enc)
        add(f"hole_{h.ref}", got >= 0.8 * want, supported=round(got / want, 2) if want else 0)

    for name, part in (printed or parts).items():
        bb = part.bounding_box()
        size = sorted([bb.size.X, bb.size.Y])
        fits = size[0] <= min(bed[:2]) and size[1] <= max(bed[:2]) and bb.size.Z <= bed[2]
        add(f"bed_{name}", fits, size_mm=[round(bb.size.X, 1), round(bb.size.Y, 1), round(bb.size.Z, 1)],
            bed_mm=list(bed))

    top = []
    for ref in board.top_access:
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
