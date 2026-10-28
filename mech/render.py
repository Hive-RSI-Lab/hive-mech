"""PNG drawings of an enclosure with its board inside, for checking by eye.

    render(board, {"base": base, "lid": lid}, "out/", explode=20)

Each view is a hidden-line projection (OCCT's, through build123d): the
enclosure's visible edges in black, its hidden ones in light grey, and the
board and its ports, top-access parts and tallest parts filled in colour
behind them. Writes iso.png (lid lifted by `explode` mm), top.png and one
view per edge that has a port (port-N.png ...): look there for each
connector (blue) framed by its opening.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MPoly  # noqa: E402

from build123d import Compound, Pos  # noqa: E402

from .fit import board_solid, part_box  # noqa: E402
from .kicad import Board  # noqa: E402

COLORS = {"board": "#66bb6a", "part": "#bdbdbd", "port": "#1e88e5", "top": "#fbc02d"}
# direction the eye looks from, and which way is up on the page
VIEWS = {"iso": ((1, -1.4, 1.1), (0, 0, 1)), "top": ((0, 0, 1), (0, 1, 0)),
         "N": ((0, 1, 0), (0, 0, 1)), "S": ((0, -1, 0), (0, 0, 1)),
         "E": ((1, 0, 0), (0, 0, 1)), "W": ((-1, 0, 0), (0, 0, 1))}


def _lines(edges, n=24):
    out = []
    for e in edges:
        try:
            pts = e.positions([i / n for i in range(n + 1)]) if e.geom_type != "LINE" else [e.position_at(0), e.position_at(1)]
        except Exception:
            continue
        out.append(([p.X for p in pts], [p.Y for p in pts]))
    return out


def _faces(shape, origin, up, look_at):
    """Each solid's projected outline as a filled hull (good enough for boxes)."""
    vis, hid = shape.project_to_viewport(origin, up, look_at)
    pts = [(p.X, p.Y) for e in list(vis) + list(hid) for p in (e.position_at(0), e.position_at(1))]
    return _hull(pts)


def _hull(pts):
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def render(board: Board, parts: dict, out: str | Path, explode: float = 20.0, views=None,
           title: str | None = None) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    title = title or Path(board.source.split()[0]).stem
    port_refs = {p.ref for p in board.ports}
    shown = [p for p in board.parts if p.ref in port_refs or p.ref in board.top_access]
    shown += [p for p in sorted(board.parts, key=lambda p: -(p.z[1] - p.z[0])) if p not in shown][:20]
    want = views or ["iso", "top"] + sorted({p.edge for p in board.ports})
    written = []
    for v in want:
        d, up = VIEWS[v]
        enc = []
        for name, part in parts.items():
            enc.append(Pos(0, 0, explode) * part if v == "iso" and name.lower().startswith("lid") else part)
        whole = Compound(children=enc)
        bb = whole.bounding_box()
        c = bb.center()
        far = 4 * max(bb.size.X, bb.size.Y, bb.size.Z)
        origin = (c.X + d[0] * far, c.Y + d[1] * far, c.Z + d[2] * far)
        look = (c.X, c.Y, c.Z)
        fig, ax = plt.subplots(figsize=(9, 7), dpi=110)
        fills = [(board_solid(board), COLORS["board"])]
        # Far parts first so near ones paint over them.
        dist = lambda p: -((p.bbox[0] + p.bbox[2]) / 2 * d[0] + (p.bbox[1] + p.bbox[3]) / 2 * d[1] + p.z[1] * d[2])
        for p in sorted(shown, key=dist, reverse=True):
            col = COLORS["port"] if p.ref in port_refs else COLORS["top"] if p.ref in board.top_access else COLORS["part"]
            fills.append((part_box(p), col))
        for shape, col in fills:
            hull = _faces(shape, origin, up, look)
            if len(hull) >= 3:
                ax.add_patch(MPoly(hull, closed=True, facecolor=col, edgecolor="#424242", linewidth=0.3, alpha=0.9))
        vis, hid = whole.project_to_viewport(origin, up, look)
        for xs, ys in _lines(hid):
            ax.plot(xs, ys, color="#cfcfcf", linewidth=0.4)
        for xs, ys in _lines(vis):
            ax.plot(xs, ys, color="black", linewidth=0.7)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(f"{title}: {v}" + (f" (lid lifted {explode:g} mm)" if v == "iso" else "")
                     + ("  blue: ports, yellow: top access" if v != "iso" else ""))
        name = f"port-{v}.png" if v in VIEWS and len(v) == 1 else f"{v}.png"
        fig.savefig(out / name, bbox_inches="tight")
        plt.close(fig)
        written.append(out / name)
    return written
