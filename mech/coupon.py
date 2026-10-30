"""A test-print coupon that calibrates mech's clearances (mech/fits.json).

    mech coupon [-o DIR]                  coupon.stl, coupon.png (a map), coupon.md, slice.json
    mech coupon --record lip=4 pilot=2 ... [--note "MK4, PETG"]
                                          what fit, by its number of dots

Every clearance in mech.box is a guess until a printer has made it. The
coupon is one flat plate, printed as it lies in about 20 minutes, with each
guess made at a few sizes round it, smallest first, each size marked by a
row of dots (1 dot the smallest). Two loose pieces print beside it: a plug
that is a corner of a lid (its plate and locating lip) and a pin the size of
a button's cap. Where nothing in the brick is round the person tries:

- lip: the plug's lip into each square socket; the tightest it goes into
  by hand and sits flat in is the lid's lip gap (Tray.lip).
- button: the pin into each round hole; the tightest it drops through and
  turns freely in is a button's clearance (Tray.button).
- clear: an M3 screw through each hole; the smallest it slides through
  without threading is the screw clearance (Tray.screws_from_below).
- pilot: an M3 self-tapper into each boss; the one it drives into firmly
  without the boss splitting is the pilot (Tray.pilot_d).
- insert: an M3 heat-set insert into each boss; the one it melts into
  flush and holds in is the insert hole (Tray(pilot_d=FITS["insert_d"])).
- bridge: four flat bridges, 10 to 25 mm; the longest that came out flat
  underneath, not drooping, is the longest bridge the fit check allows.

Anything the person didn't try stays as it was. `--record` writes the sizes
picked into mech/fits.json (MECH_FITS) with the date and the note; commit
it and every enclosure after uses them.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from build123d import Part

from .box import block, cylinder, rounded
from .fits import FITS, path as fits_path

PLATE = 1.6      # the plate's thickness
LIP_W = 8.0      # the plug's lip, outside, square
LIP_T = 1.2      # its thickness (Tray.lip's)
LIP_H = 1.5      # its height: it goes all the way into a socket
LIP_R = 1.0      # its corner radius
PIN_D = 5.0      # a button's cap (Tray.button's d)
BOSS_D = 7.0     # a screw boss (Tray.boss_d)
BOSS_H = 7.6     # a boss's top over the bed: 6 mm of hole over the plate
BRIDGE_Z = 3.0   # a bridge's underside over the plate
DOT_D, DOT_PITCH, DOT_DEPTH = 0.9, 1.5, 0.6
MARGIN = 2.0     # plate round a row's features

# Each test: the key in fits.json, the sizes tried (smallest first: the
# current guess among them), what to do, and what to record.
TESTS = {
    "lip": ("lip_gap", [0.10, 0.15, 0.20, 0.25, 0.30, 0.40],
            "push the plug's lip into each square socket",
            "the tightest socket it goes into by hand and sits flat in"),
    "button": ("button_clear", [0.15, 0.20, 0.30, 0.45],
               "drop the pin into each round hole of the first group",
               "the tightest hole it drops through and turns freely in"),
    "clear": ("clear_d", [3.2, 3.4, 3.6],
              "push an M3 screw through each hole of the second group",
              "the smallest hole it slides through without threading"),
    "pilot": ("pilot_d", [2.3, 2.5, 2.7],
              "drive an M3 self-tapping screw into each of the first three bosses",
              "the one it goes into firmly without the boss splitting"),
    "insert": ("insert_d", [3.8, 4.0, 4.2],
               "press a hot M3 heat-set insert into each of the last three bosses",
               "the one it melts into flush and holds in"),
    "bridge": ("max_bridge", [10.0, 15.0, 20.0],
               "look under the three bridges",
               "the longest that is flat underneath, not drooping"),
}

# Rows, front to back (y centres). Each row's dots run behind it (toward the
# back), at DOTS[row], so a row of dots belongs to the row in front of it.
# The plate is a stair of rows, each as long as it needs: the long lip row
# in front, so the plate's notch is at the back right.
Y_LOOSE, Y_LIP, Y_HOLE, Y_BOSS, Y_BRIDGE = -9.0, 6.4, 18.0, 28.0, 36.5
DOTS = {Y_LIP: 12.4, Y_HOLE: 22.5, Y_BOSS: 33.1, Y_BRIDGE: 39.6}
X0 = 6.4


def features() -> list[dict]:
    """Every graded feature: test, index n (its dots), size, where (x, y)."""
    out = []
    for i, g in enumerate(TESTS["lip"][1]):
        out.append({"test": "lip", "n": i + 1, "size": g, "x": X0 + 11.5 * i, "y": Y_LIP})
    x = 5.0
    for test, pitch, gap in (("button", 8.5, 7.0), ("clear", 6.5, 0.0)):
        for i, s in enumerate(TESTS[test][1]):
            out.append({"test": test, "n": i + 1, "size": s, "x": x, "y": Y_HOLE})
            x += pitch
        x += gap - pitch
    x = 5.5
    for test in ("pilot", "insert"):
        for i, s in enumerate(TESTS[test][1]):
            out.append({"test": test, "n": i + 1, "size": s, "x": x, "y": Y_BOSS})
            x += 9.0
        x += 2.0
    x = 1.5
    for i, s in enumerate(TESTS["bridge"][1]):
        out.append({"test": "bridge", "n": i + 1, "size": s, "x": x + 2.5 + s / 2, "y": Y_BRIDGE})
        x += 2.5 + s
    return out


def rows() -> list[tuple]:
    """The plate as rows: (x0, y0, x1, y1) each, front to back."""
    fs = features()
    out, y0 = [], 0.0
    for y in (Y_LIP, Y_HOLE, Y_BOSS, Y_BRIDGE):
        x1 = max(f["x"] + _half(f) for f in fs if f["y"] == y) + MARGIN
        y1 = DOTS[y] + DOT_D / 2 + 1.2
        out.append((0.0, y0, x1, y1))
        y0 = y1
    return out


def _half(f: dict) -> float:
    """How far a feature reaches from its centre along the row."""
    t, s = f["test"], f["size"]
    return {"lip": LIP_W / 2 + s, "button": PIN_D / 2 + s, "clear": s / 2,
            "pilot": BOSS_D / 2, "insert": BOSS_D / 2}.get(t, s / 2 + 2.5)


def _dots(n: int, x: float, y: float) -> Part:
    x0 = x - (n - 1) * DOT_PITCH / 2
    return sum((cylinder(x0 + i * DOT_PITCH, y, PLATE - DOT_DEPTH, PLATE + 1, DOT_D) for i in range(1, n)),
               cylinder(x0, y, PLATE - DOT_DEPTH, PLATE + 1, DOT_D))


def build() -> dict:
    """{"plate": ..., "plug": ..., "pin": ...} as they lie on the bed."""
    rs = rows()
    plate = rounded(*rs[0][:2], *rs[0][2:], 0, PLATE, 2.0)
    for r in rs[1:]:
        plate += rounded(r[0], r[1] - 2.5, r[2], r[3], 0, PLATE, 2.0)
    bridge_top = PLATE + BRIDGE_Z
    for f in features():
        t, s, x, y, n = f["test"], f["size"], f["x"], f["y"], f["n"]
        if t == "lip":
            w = LIP_W + 2 * s
            plate -= rounded(x - w / 2, y - w / 2, x + w / 2, y + w / 2, -1, PLATE + 1, LIP_R + s)
            plate -= _dots(n, x, DOTS[y])
        elif t in ("button", "clear"):
            d = PIN_D + 2 * s if t == "button" else s
            plate -= cylinder(x, y, -1, PLATE + 1, d)
            plate -= _dots(n, x, DOTS[y])
        elif t in ("pilot", "insert"):
            plate += cylinder(x, y, PLATE - 0.01, BOSS_H, BOSS_D)
            # 6 mm deep: an M3 x 8 screw's bite, a 5.7 mm insert and a bit
            plate -= cylinder(x, y, PLATE, BOSS_H + 1, s)
            plate -= _dots(n, x, DOTS[y])
        elif t == "bridge":
            x0, x1 = x - s / 2, x + s / 2
            for px in (x0 - 2.5, x1):
                plate += block(px, y - 1.5, PLATE - 0.01, px + 2.5, y + 1.5, bridge_top + 0.6)
            plate += block(x0 - 0.01, y - 1.5, bridge_top, x1 + 0.01, y + 1.5, bridge_top + 0.6)
            plate -= _dots(n, x, DOTS[y])
    # The plug: a lid's corner as a lid prints (plate down, lip up).
    px, py = X0, Y_LOOSE
    plug = rounded(px - 6, py - 6, px + 6, py + 6, 0, 1.6, 2.0)
    ring = rounded(px - LIP_W / 2, py - LIP_W / 2, px + LIP_W / 2, py + LIP_W / 2, 1.59, 1.6 + LIP_H, LIP_R)
    i = LIP_W / 2 - LIP_T
    ring -= rounded(px - i, py - i, px + i, py + i, 1.0, 2.6 + LIP_H, max(0.1, LIP_R - LIP_T))
    plug += ring
    # The pin: a button's cap as a button prints, on its end.
    pin = cylinder(X0 + 14, Y_LOOSE, 0, 6.0, PIN_D)
    return {"plate": plate, "plug": plug, "pin": pin}


def guide(fits: dict | None = None) -> str:
    """coupon.md: how to print it, what to try, and how to record it."""
    fits = fits or FITS
    lines = ["# mech's test-print coupon", "",
             "One print calibrates the clearances every mech enclosure uses (`mech/fits.json`).",
             "Print `coupon.stl` as it lies (plate, plug and pin together; PETG, 0.2 mm layers,",
             "the printer and filament the enclosures will use, no support), then try each test.",
             "Sizes go smallest first, left to right; the dots beside each one count it (1 dot the",
             "smallest). `coupon.png` is the map; the plate's long row is at the front.", "",
             "| test | try | record | sizes (dots: mm) | now |", "|---|---|---|---|---|"]
    for name, (key, sizes, how, what) in TESTS.items():
        unit = " per side" if key in ("lip_gap", "button_clear") else ""
        sz = ", ".join(f"{i + 1}: {s:g}" for i, s in enumerate(sizes))
        lines.append(f"| {name} | {how} | {what} | {sz}{unit} | {fits[key]:g} |")
    lines += ["", "Then tell the Hive the dots, a test you didn't try left out, e.g.",
              "`lip 4, button 3, clear 2, pilot 2, insert 2, bridge 3, printer MK4, PETG`;",
              "an agent runs `mech coupon --record lip=4 button=3 clear=2 pilot=2 insert=2 bridge=3",
              "--note \"MK4, PETG\"` and commits mech/fits.json.",
              "If even the loosest size is too tight (or the tightest too loose), say so: the",
              "next coupon moves its sizes.", ""]
    return "\n".join(lines)


def draw(png: Path) -> None:
    """coupon.png: the plate from above, every feature named."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

    fig, ax = plt.subplots(figsize=(10, 7.5))
    rs = rows()
    for x0, y0, x1, y1 in rs:
        ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0, boxstyle="round,pad=0,rounding_size=2",
                                    fill=False, lw=1.5))
    for f in features():
        t, s, x, y, n = f["test"], f["size"], f["x"], f["y"], f["n"]
        if t == "lip":
            w = LIP_W + 2 * s
            ax.add_patch(Rectangle((x - w / 2, y - w / 2), w, w, fill=False))
        elif t in ("button", "clear"):
            ax.add_patch(Circle((x, y), (PIN_D + 2 * s if t == "button" else s) / 2, fill=False))
        elif t in ("pilot", "insert"):
            ax.add_patch(Circle((x, y), BOSS_D / 2, fill=False))
            ax.add_patch(Circle((x, y), s / 2, fill=False))
        else:
            ax.add_patch(Rectangle((x - s / 2 - 2.5, y - 1.5), s + 5, 3, fill=False))
        x0 = x - (n - 1) * DOT_PITCH / 2
        for i in range(n):
            ax.add_patch(Circle((x0 + i * DOT_PITCH, DOTS[y]), DOT_D / 2, color="black"))
        ax.text(x, DOTS[y] + 0.9, f"{t} {s:g}", ha="center", va="bottom", fontsize=6.5)
    ax.add_patch(Rectangle((X0 - 6, Y_LOOSE - 6), 12, 12, fill=False))
    ax.add_patch(Rectangle((X0 - LIP_W / 2, Y_LOOSE - LIP_W / 2), LIP_W, LIP_W, fill=False))
    ax.text(X0, Y_LOOSE, "plug", ha="center", va="center", fontsize=7)
    ax.add_patch(Circle((X0 + 14, Y_LOOSE), PIN_D / 2, fill=False))
    ax.text(X0 + 14, Y_LOOSE - 4.5, "pin", ha="center", va="center", fontsize=7)
    ax.set_xlim(-3, max(r[2] for r in rs) + 3)
    ax.set_ylim(Y_LOOSE - 9, rs[-1][3] + 3)
    ax.set_aspect("equal")
    ax.set_title("mech coupon: sizes smallest first, the dots count them (mm; lip and button per side)")
    ax.axis("off")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def make(out: Path, slice_it: bool = True, log=print) -> dict:
    """Write coupon.stl (and each piece's own), coupon.png, coupon.md and,
    with a slicer, slice.json. Returns the slice (or {})."""
    from build123d import Compound, export_stl
    out.mkdir(parents=True, exist_ok=True)
    parts = build()
    export_stl(Compound(children=list(parts.values())), str(out / "coupon.stl"))
    draw(out / "coupon.png")
    md = guide()
    sliced = {}
    if slice_it:
        from . import slicer
        try:
            slicer.ensure(log=log)
        except Exception as e:
            log(f"slice: skipped, no slicer ({e})")
        else:
            sliced = slicer.slice_parts({"coupon": out / "coupon.stl"})
            (out / "slice.json").write_text(json.dumps(sliced, indent=1) + "\n", encoding="utf-8")
            md += slicer.markdown(sliced)
    (out / "coupon.md").write_text(md, encoding="utf-8")
    return sliced


def record(picks: dict, note: str = "", p: Path | None = None, today: str | None = None) -> dict:
    """Write what fit into fits.json: picks is {test: dots}, as the person
    counts them. Returns the new fits."""
    p = p or fits_path()
    fits = json.loads(p.read_text(encoding="utf-8"))
    took = {}
    for test, n in picks.items():
        if test not in TESTS:
            raise ValueError(f"coupon: no test {test!r} (tests: {', '.join(TESTS)})")
        key, sizes = TESTS[test][0], TESTS[test][1]
        if not 1 <= int(n) <= len(sizes):
            raise ValueError(f"coupon: {test} has sizes 1 to {len(sizes)}, not {n}")
        fits[key] = sizes[int(n) - 1]
        took[test] = int(n)
    if not took:
        raise ValueError("coupon: record what? e.g. lip=4 pilot=2")
    cal = dict(fits.get("calibrated") or {})
    cal.update({"date": today or datetime.date.today().isoformat(), "note": note or cal.get("note", ""),
                "dots": {**cal.get("dots", {}), **took}})
    fits["calibrated"] = cal
    p.write_text(json.dumps(fits, indent=1) + "\n", encoding="utf-8")
    return fits
