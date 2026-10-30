"""Write out/<tier>/fit.md from the fit.json `mech build` left there: python3 enclosure/fit_md.py lite plus ultra"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"

BOARDS = {"lite": "boards/lite-main"}   # the rest are around boards/core

for tier in sys.argv[1:]:
    r = json.loads((OUT / tier / "fit.json").read_text())
    rows = ["| check | result | measured |", "|---|---|---|"]
    for c in r["checks"]:
        info = ", ".join(f"{k}={v}" for k, v in c.items() if k not in ("name", "ok"))
        rows.append(f"| {c['name']} | {'pass' if c['ok'] else 'fail'} | {info} |")
    # mech checks parts against the plastic, not against each other: the boxes the scripts add
    # (posts, heatsinks, the front board) must also clear the board's own parts.
    b = json.loads((OUT / tier / "board.json").read_text())
    def bx(p):
        x0, y0, x1, y1 = p["bbox"]
        return [x0, y0, p["z"][0], x1, y1, p["z"][1]]
    added = [p for p in b["parts"] if p["footprint"].startswith("enclosure:")]
    real = [p for p in b["parts"] if not p["footprint"].startswith("enclosure:")]
    clash = []
    for a in added:
        A = bx(a)
        for c in real + [q for q in added if q is not a and not (q["ref"].startswith("F") and a["ref"].startswith("F"))]:
            C = bx(c)
            if all(min(A[i + 3], C[i + 3]) - max(A[i], C[i]) > 0.01 for i in range(3)):
                clash.append(f"{a['ref']}/{c['ref']}")
    clash = sorted(set("/".join(sorted(x.split("/"))) for x in clash))
    rows.append(f"| added_boxes_clear | {'fail' if clash else 'pass'} | {len(added)} boxes (posts, heatsinks, front board) "
                f"vs the board's parts; clashes={clash} |")
    top = [f"- {t['ref']}: " + ("open above" if t["open_above"] else "under " + ", ".join(t["under"]))
           for t in r.get("top_access", [])]
    mat = [f"- {n}: {m['volume_cm3']} cm3, {m['solid_petg_g']} g PETG if solid" for n, m in r["material"].items()]
    n, ok = len(r["checks"]) + 1, sum(c["ok"] for c in r["checks"]) + (0 if clash else 1)
    text = (f"# Fit check: {tier}\n\n`mech build enclosure/{tier}.py -o enclosure/out/{tier}` "
            f"against {BOARDS.get(tier, 'boards/core')} (see enclosure/README.md). {ok}/{n} pass.\n\n"
            + "\n".join(rows) + "\n\n## What is over each top-access part\n\n" + "\n".join(top)
            + "\n\n## Material\n\n" + "\n".join(mat) + "\n")
    (OUT / tier / "fit.md").write_text(text)
    print(f"{tier}: {ok}/{n}")
