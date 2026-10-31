"""mech score: how many planted enclosure faults mech's fit check catches.

    mech score              (the corpus; mech score --product: the product's pass ratio)

The corpus is a few known-good enclosures (BASES: the module's examples and
the pinned product's own scripts, each built on its board), and FAULTS: real
mistakes, each planted in code on a fresh copy of a base. A fault names the
check it is for (its `expect`, a fit check name); it is caught when that
check fails with the fault planted and passed on the clean base (only that
check is run on it: fit.check's `only`). A check
failing on a clean base is a false alarm. The score is

    100 x (faults caught - false alarms) / faults planted

higher is better. A fault class mech has no check for yet (a switch's
opening filled in: mech lists top access but doesn't judge it) counts as
missed, so a new check raises the score, and a check that cries wolf on a
good box lowers it. Nothing is stored but this code: the faults are cut,
added or moved with build123d at run time.

Faults in the assembled frame (the board's: z = 0 its bottom face) go
through fit.check as a build would; printability faults are planted on a
printed part as it lies on the bed, beside the part (y < 0), where they
can't touch anything else.
"""
from __future__ import annotations

import copy
import multiprocessing
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from build123d import Plane, Polygon, Pos, extrude

from .box import block, cylinder
from .cli import ROOT, printed_parts, run_script

EXAMPLES = ROOT / "examples"
# Known good: each passes every fit check, and a review of its build says it
# should. The product's own (Lite, Plus, and Ultra with ultra-front behind its
# front panel) come in with the pin; Ultra passes 55/55 since hifi-receiver
# 40278fd fixed its overhangs, bridges and walls.
BASES = {
    "tiny": EXAMPLES / "tiny" / "enclosure.py",
    "lite-main": EXAMPLES / "lite-main" / "enclosure.py",
    "product-lite": EXAMPLES / "product" / "enclosure" / "lite.py",
    "product-plus": EXAMPLES / "product" / "enclosure" / "plus.py",
    "product-ultra": EXAMPLES / "product" / "enclosure" / "ultra.py",
}


@dataclass
class Case:
    """One enclosure as fit.check sees it."""
    board: object
    parts: dict
    printed: dict
    fit: dict
    mod: object

    def check(self, only: set | None = None) -> dict:
        from . import fit
        return fit.check(self.board, self.parts, printed=self.printed, only=only, **self.fit)

    @property
    def lid(self) -> str:
        return self.fit.get("lid", "lid")

    @property
    def base(self) -> str:
        b = self.fit.get("base", "base")
        return b if b in self.parts else next(iter(self.parts))

    def outer(self):
        from build123d import Compound
        ps = list(self.parts.values())
        return (ps[0] if len(ps) == 1 else Compound(children=ps)).bounding_box()

    def assembled(self, parts: dict, board=None) -> "Case":
        """This case with parts (and board) replaced, printed again as built."""
        return Case(board or self.board, parts, printed_parts(self.mod, parts), self.fit, self.mod)

    def on_bed(self, name: str, extra) -> "Case":
        """This case with extra fused to printed part name, beside it on the bed."""
        printed = dict(self.printed)
        printed[name] = printed[name] + extra
        return Case(self.board, self.parts, printed, self.fit, self.mod)


def load_base(script: Path) -> Case:
    mod, board, parts = run_script(script)
    return Case(board, parts, printed_parts(mod, parts), dict(getattr(mod, "FIT", {})), mod)


# A fault: (case) -> (planted case, the check meant to catch it), or None
# where the base has nothing to plant it on (no port, no top access part).
Plant = Callable[[Case], "tuple[Case, str] | None"]


def _with(case: Case, name: str, shape) -> dict:
    parts = dict(case.parts)
    parts[name] = parts[name] + shape
    return parts


def port_blocked(c: Case):
    """A port's opening left closed: a plate over it at the wall's outside."""
    if not c.board.ports:
        return None
    p = c.board.ports[0]
    u0, u1, z0, z1 = p.opening()
    bb = c.outer()
    if p.edge == "E":
        a = max(bb.max.X - 1.5, p.plane + 0.2)
        plate = block(a, u0, z0, a + 1.5, u1, z1)
    elif p.edge == "W":
        a = min(bb.min.X + 1.5, p.plane - 0.2)
        plate = block(a - 1.5, u0, z0, a, u1, z1)
    elif p.edge == "N":
        a = max(bb.max.Y - 1.5, p.plane + 0.2)
        plate = block(u0, a, z0, u1, a + 1.5, z1)
    else:
        a = min(bb.min.Y + 1.5, p.plane - 0.2)
        plate = block(u0, a - 1.5, z0, u1, a, z1)
    return c.assembled(_with(c, c.base, plate)), f"port_{p.ref}"


def port_deep(c: Case):
    """A port at the bottom of a deep collar: the wall round it 8 mm thick, past what a plug reaches."""
    if not c.board.ports:
        return None
    p = c.board.ports[0]
    u0, u1, z0, z1 = p.opening()
    bb = c.outer()
    d, m = 8.0, 2.0
    if p.edge in "EW":
        x = bb.max.X if p.edge == "E" else bb.min.X - d
        ring = block(x, u0 - m, z0 - m, x + d, u1 + m, z1 + m) - block(x - 1, u0, z0, x + d + 1, u1, z1)
    else:
        y = bb.max.Y if p.edge == "N" else bb.min.Y - d
        ring = block(u0 - m, y, z0 - m, u1 + m, y + d, z1 + m) - block(u0, y - 1, z0, u1, y + d + 1, z1)
    return c.assembled(_with(c, c.base, ring)), f"port_{p.ref}"


def standoff_short(c: Case):
    """A standoff 1 mm short: the board's first mounting hole sits over air."""
    if not c.board.holes:
        return None
    h = c.board.holes[0]
    gap = cylinder(h.x, h.y, -1.0, 0.0, max(h.pad, h.drill + 2.0) + 1.0)
    return c.assembled({n: p - gap for n, p in c.parts.items()}), f"hole_{h.ref}"


def hole_moved(c: Case):
    """The boss isn't under the hole: the board's first hole 8 mm off, toward the board's middle."""
    if not c.board.holes:
        return None
    b = copy.deepcopy(c.board)
    h = b.holes[0]
    h.x += 8.0 if h.x < b.size[0] / 2 else -8.0
    return c.assembled(dict(c.parts), b), f"hole_{h.ref}"


def pilot_filled(c: Case):
    """The screw hole left out: the boss under the first mounting hole solid to the board."""
    if not c.board.holes:
        return None
    h = c.board.holes[0]
    plug = cylinder(h.x, h.y, -4.0, 0.0, min(2.0, h.drill))
    return c.assembled(_with(c, c.base, plug)), f"screw_{h.ref}"


def board_tight(c: Case):
    """No room to drop the board in: a pad on the wall touching its edge, 10 mm along it."""
    xs = [p[0] for p in c.board.outline]
    ys = [p[1] for p in c.board.outline]
    w, h = max(xs), max(ys)
    taken = {p.edge for p in c.board.ports}
    for e in "EWNS":
        if e in taken:
            continue
        if e in "EW":
            x = w if e == "E" else min(xs) - 3.0
            on = [p[1] for p in c.board.outline if abs(p[0] - (w if e == "E" else min(xs))) < 0.01]
            if len(on) < 2:
                continue
            m = (min(on) + max(on)) / 2
            pad = block(x, m - 5, 0.0, x + 3.0, m + 5, c.board.thickness)
        else:
            y = h if e == "N" else min(ys) - 3.0
            on = [p[0] for p in c.board.outline if abs(p[1] - (h if e == "N" else min(ys))) < 0.01]
            if len(on) < 2:
                continue
            m = (min(on) + max(on)) / 2
            pad = block(m - 5, y, 0.0, m + 5, y + 3.0, c.board.thickness)
        return c.assembled(_with(c, c.base, pad)), "board_gap"
    return None


def lid_short(c: Case):
    """The lid stops 15 mm short of one end of the box."""
    if c.lid not in c.parts or c.fit.get("base", "base") not in c.parts:
        return None
    bb = c.parts[c.lid].bounding_box()
    cut = block(bb.max.X - 15, bb.min.Y - 1, bb.min.Z - 1, bb.max.X + 1, bb.max.Y + 1, bb.max.Z + 1)
    parts = dict(c.parts)
    parts[c.lid] = parts[c.lid] - cut
    return c.assembled(parts), "lid_covers"


def board_hit(c: Case):
    """A rib that runs into the board: a 2 mm post up through it, inside its outline."""
    from shapely.geometry import Polygon as SPoly
    pt = SPoly(c.board.outline).buffer(-3).representative_point()
    post = block(pt.x - 1, pt.y - 1, -0.5, pt.x + 1, pt.y + 1, c.board.thickness / 2)
    return c.assembled(_with(c, c.base, post)), "board_clear"


def lid_hits_part(c: Case):
    """The lid's underside too low over the tallest part: a pin 1 mm into it."""
    top = [p for p in c.board.parts if p.side == "top" and p.z[1] > p.z[0]]
    if not top:
        return None
    p = max(top, key=lambda q: q.z[1])
    x, y = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
    pin = block(x - 1, y - 1, p.z[1] - 1.0, x + 1, y + 1, p.z[1] + 0.5)
    into = c.lid if c.lid in c.parts else c.base
    return c.assembled(_with(c, into, pin)), "parts_clear"


def floor_hits_leads(c: Case):
    """The floor too high under a through-hole part: a pad 0.5 mm into its leads."""
    tht = [p for p in c.board.parts if p.side == "top" and p.under > 0.5]
    if not tht:
        return None
    p = max(tht, key=lambda q: q.under)
    x, y = (max(p.bbox[0], 0) + min(p.bbox[2], c.board.size[0])) / 2, \
        (max(p.bbox[1], 0) + min(p.bbox[3], c.board.size[1])) / 2
    pad = block(x - 1, y - 1, -p.under - 1.0, x + 1, y + 1, -p.under + 0.5)
    return c.assembled(_with(c, c.base, pad)), "parts_clear"


def parts_overlap(c: Case):
    """Two printed parts in each other: the lid carries a copy of the base's floor."""
    if len(c.parts) < 2:
        return None
    other = c.lid if c.lid in c.parts and c.lid != c.base else next(n for n in c.parts if n != c.base)
    bb = c.parts[c.base].bounding_box()
    floor = c.parts[c.base] & block(bb.min.X - 1, bb.min.Y - 1, bb.min.Z - 1, bb.max.X + 1, bb.max.Y + 1,
                                    bb.min.Z + 1.0)
    return c.assembled(_with(c, other, floor)), "parts_apart"


def added_hits_part(c: Case):
    """Something the script adds (a heatsink) placed on top of one of the board's parts."""
    top = [p for p in c.board.parts if p.side == "top" and p.z[1] - p.z[0] > 1.0 and not p.footprint.startswith("enclosure:")]
    if not top:
        return None
    b = copy.deepcopy(c.board)
    p = min(top, key=lambda q: q.z[1])
    x, y = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
    b.add_box("HS_PLANTED", x - 1, y - 1, p.z[1] - 0.5, x + 1, y + 1, p.z[1] + 0.5, "heatsink")
    return c.assembled(dict(c.parts), b), "added_parts_clear"


def top_access_covered(c: Case):
    """A switch or LED meant to be reached from above sealed in under a solid lid."""
    from . import fit
    if c.lid not in c.parts:
        return None
    bb = c.outer()
    for ref in c.board.top_access:
        p = c.board.part(ref)
        x, y = (p.bbox[0] + p.bbox[2]) / 2, (p.bbox[1] + p.bbox[3]) / 2
        col = cylinder(x, y, p.z[1] + 0.5, bb.max.Z + 1, 1.0)
        if any(fit._overlap(col, e) >= fit.EPS for e in c.parts.values()):
            continue  # a button or something already over it: not open to seal
        plug = cylinder(x, y, p.z[1] + 0.5, bb.max.Z, 3.0)
        return c.assembled(_with(c, c.lid, plug)), f"access_{ref}"
    return None


def too_big_for_bed(c: Case):
    """A part longer than the bed: a 240 mm runner beside it."""
    return c.on_bed(c.base, block(0, -16, 0, 240, -10, 6)), f"bed_{c.base}"


def overhang(c: Case):
    """A shelf under-cut at 27 degrees from flat: needs support."""
    face = Plane.XZ * Polygon((0, 0), (6, 0), (6, 10), (-12, 10), (-12, 6), align=None)
    wedge = extrude(face, amount=10)  # Plane.XZ's normal is -Y: from y = 0 to -10
    return c.on_bed(c.base, Pos(20, -12, 0) * wedge), f"overhang_{c.base}"


def long_bridge(c: Case):
    """A 30 mm flat span between two pillars: sags."""
    span = block(0, -22, 0, 4, -12, 8) + block(34, -22, 0, 38, -12, 8) + block(0, -22, 8, 38, -12, 11)
    return c.on_bed(c.base, span), f"bridge_{c.base}"


def ledge(c: Case):
    """A shelf 8 mm out from a pillar, printed in the air."""
    shelf = block(0, -22, 0, 4, -12, 8) + block(-8, -22, 8, 4, -12, 11)
    return c.on_bed(c.base, Pos(20, 0, 0) * shelf), f"bridge_{c.base}"


def island(c: Case):
    """A block starting in mid-air, nothing under it."""
    return c.on_bed(c.base, block(10, -22, 5, 20, -12, 8)), f"bridge_{c.base}"


def thin_fin(c: Case):
    """A fin 0.5 mm thick: under two nozzle widths, it won't print."""
    return c.on_bed(c.base, block(0, -14, 0, 20, -13.5, 8)), f"wall_{c.base}"


FAULTS: dict[str, Plant] = {f.__name__: f for f in (
    port_blocked, port_deep, standoff_short, hole_moved, pilot_filled, lid_short, board_tight, board_hit, lid_hits_part,
    floor_hits_leads, parts_overlap, added_hits_part, top_access_covered, too_big_for_bed,
    overhang, long_bridge, ledge, island, thin_fin)}


def _failing(r: dict) -> set[str]:
    return {x["name"] for x in r["checks"] if not x["ok"]}


# The bases run() loaded, read by _plant in the pool's forked workers (OCP
# shapes don't pickle; a fork inherits them).
_LOADED: dict[str, tuple[Case, set]] = {}


def _plant(task: tuple[str, str, Plant]) -> tuple[str, dict | None]:
    """One fault planted on its loaded base and checked: (line, result or None if skipped)."""
    bname, fname, plant = task
    base, clean = _LOADED[bname]
    try:
        got = plant(base)
    except Exception as e:
        got, why = None, f"can't plant: {e}"
    else:
        why = "nothing to plant it on"
    if got is None:
        return f"{bname}/{fname}: skipped ({why})", None
    case, expect = got
    try:
        new = _failing(case.check({expect})) - clean
    except Exception as e:
        new, err = set(), str(e)
    else:
        err = ""
    hit = expect in new
    line = (f"caught by {expect}" if hit else
            f"missed (meant for {expect}; check error: {err})" if err else f"missed (meant for {expect})")
    return f"{bname}/{fname}: {line}", {"base": bname, "fault": fname, "expect": expect, "caught": hit}


def jobs() -> int:
    """Processes for the planted faults: MECH_JOBS, else this machine's cores."""
    n = os.environ.get("MECH_JOBS")
    return max(1, int(n) if n else len(os.sched_getaffinity(0)))


def run(bases: dict[str, Path] = BASES, faults: dict[str, Plant] = FAULTS, out=sys.stdout) -> dict:
    """{"caught", "faults", "false_alarms", "results"}; prints a line per fault.
    Each base is built and checked clean here, once; its faults are planted
    and checked in jobs() forked processes (each fault on its own, so the
    numbers are the same as one at a time), and printed in order."""
    alarms = 0
    results, tasks = [], []
    _LOADED.clear()
    for bname, script in bases.items():
        t0 = time.time()
        try:
            base = load_base(script)
            clean = _failing(base.check())
        except Exception as e:  # a base that doesn't build alarms once and plants nothing
            print(f"{bname}: does not build: {e}", file=out)
            alarms += 1
            continue
        alarms += len(clean)
        for x in sorted(clean):
            print(f"{bname}: false alarm on the clean base: {x}", file=out)
        print(f"{bname}: built and checked clean in {time.time() - t0:.0f} s", file=out)
        _LOADED[bname] = (base, clean)
        tasks += [(bname, fname, plant) for fname, plant in faults.items()]
    t0 = time.time()
    n = min(jobs(), len(tasks))
    pool = multiprocessing.get_context("fork").Pool(n) if n > 1 else None
    try:
        for line, r in (pool.imap(_plant, tasks) if pool else map(_plant, tasks)):
            print(line, file=out)
            if r:
                results.append(r)
    finally:
        if pool:
            pool.terminate()
        _LOADED.clear()
    print(f"{len(tasks)} faults in {time.time() - t0:.0f} s on {max(n, 1)} process(es)", file=out)
    total = len(results)
    caught = sum(r["caught"] for r in results)
    if not total:
        raise SystemExit("mech score: no faults planted")
    print(f"{caught}/{total} faults caught, {alarms} false alarm(s)", file=out)
    return {"caught": caught, "faults": total, "false_alarms": alarms, "results": results}


def score(r: dict) -> float:
    return 100.0 * (r["caught"] - r["false_alarms"]) / r["faults"]
