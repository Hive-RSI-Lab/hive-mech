"""mech's commands (bin/mech runs these with the venv's python).

    mech board <file.kicad_pcb> [-o board.json] [--heights heights.json]
                                                 what the enclosure needs, as JSON
    mech build <enclosure.py> [-o DIR] [--no-render] [--no-slice] [--board B]
                                                 run it, export, fit-check, slice, render
    mech slice <part.stl ...> [--install]        PrusaSlicer's grams, support and time
    mech fit <enclosure.py> [--board B]          the fit check alone
    mech pin <clone> [--sha REV]                 pin the product's boards for the score
    mech score [--product]                       planted faults caught on known-good boxes (mech.corpus);
                                                 --product: the pinned product boards, graded by their boxes
    mech onshape ...                             see docs/onshape.md

An enclosure script is plain Python with build123d:

    BOARD = "board.json"                # or a .kicad_pcb; relative to the script
    PRINT = {"lid": "flip"}             # optional: parts turned for the bed
    FIT = {"max_recess": 3.0}           # optional: fit.check's keyword args
    SLICE = {"fill_density": "15%",     # optional: PrusaSlicer options over
             "lid": {"perimeters": 3}}  # mech/slicer.ini, all parts or one
    def build(board):                   # board: mech.kicad.Board
        return {"base": ..., "lid": ...}  # assembled, in the board's frame

`build` writes into DIR (default: out/ beside the script): <part>.stl as
printed, enclosure.step (the assembly with the board as a slab, for
Onshape or any CAD), board.json, fit.json, fit.md (the same report as a
table, one pass/fail row per check), slice.json (each part sliced by
PrusaSlicer, mech.slicer: filament grams, support volume, print time; also
a table in fit.md) and the renders. --no-slice or MECH_SLICE=0 skips it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

from . import slicer

ROOT = Path(__file__).resolve().parent.parent


def load_script(path: Path):
    spec = importlib.util.spec_from_file_location(f"enclosure_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(path.parent))
    if not hasattr(mod, "build"):
        raise SystemExit(f"{path}: needs a build(board) function")
    return mod


def run_script(script: Path, board_arg: str | None = None):
    from .kicad import load
    mod = load_script(script)
    src = board_arg or getattr(mod, "BOARD", None)
    if not src:
        raise SystemExit(f"{script}: say which board (BOARD = ... or --board)")
    bpath = Path(src)
    if not bpath.is_absolute() and not bpath.exists():
        bpath = script.parent / bpath
    board = load(bpath)
    parts = mod.build(board)
    if not isinstance(parts, dict) or not parts:
        raise SystemExit(f"{script}: build() must return a dict of name -> Part")
    return mod, board, parts


def printed_parts(mod, parts: dict) -> dict:
    from build123d import Pos, Rot
    out = {}
    for name, part in parts.items():
        how = getattr(mod, "PRINT", {}).get(name)
        p = part
        if how == "flip":
            p = Rot(180, 0, 0) * p
        elif isinstance(how, (tuple, list)):
            p = Rot(*how) * p
        bb = p.bounding_box()
        out[name] = Pos(-bb.min.X, -bb.min.Y, -bb.min.Z) * p
    return out


def cmd_board(a) -> int:
    from .kicad import read_board, read_heights
    b = read_board(a.pcb, heights=read_heights(a.heights) if a.heights else None)
    text = b.to_json()
    if a.output:
        Path(a.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    w, h = b.size
    print(f"board: {w} x {h} x {b.thickness} mm, {len(b.holes)} holes, {len(b.parts)} parts "
          f"(tallest top {b.top_height():.1f} mm, bottom {b.bottom_depth():.1f} mm), "
          f"{len(b.ports)} ports: {', '.join(f'{p.ref}@{p.edge}' for p in b.ports)}; "
          f"top access: {', '.join(b.top_access) or '-'}", file=sys.stderr)
    for wmsg in b.warnings:
        print(f"warning: {wmsg}", file=sys.stderr)
    return 0


def cmd_build(a, export=True) -> int:
    from . import fit
    script = Path(a.script).resolve()
    t0 = time.time()
    mod, board, parts = run_script(script, a.board)
    t_build = time.time() - t0
    printed = printed_parts(mod, parts)
    report = fit.check(board, parts, printed=printed, **getattr(mod, "FIT", {}))
    report["build_s"] = round(t_build, 1)
    print(fit.summary(report))
    for wmsg in board.warnings:
        print(f"warning: {wmsg}")
    if export:
        from build123d import Compound, export_step, export_stl
        out = Path(a.output) if a.output else script.parent / "out"
        out.mkdir(parents=True, exist_ok=True)
        for name, p in printed.items():
            export_stl(p, str(out / f"{name}.stl"))
        asm = []
        for name, p in parts.items():
            p.label = name
            asm.append(p)
        slab = fit.board_solid(board)
        slab.label = "board"
        asm.append(slab)
        export_step(Compound(children=asm), str(out / "enclosure.step"))
        (out / "board.json").write_text(board.to_json() + "\n", encoding="utf-8")
        (out / "fit.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        md = fit.markdown(report, script.parent.name)
        if not a.no_slice and os.environ.get("MECH_SLICE", "1") != "0":
            sliced = slice_printed(mod, {n: out / f"{n}.stl" for n in printed})
            if sliced is not None:
                (out / "slice.json").write_text(json.dumps(sliced, indent=1) + "\n", encoding="utf-8")
                md += slicer.markdown(sliced)
                print(slicer.summary(sliced))
        (out / "fit.md").write_text(md, encoding="utf-8")
        if not a.no_render:
            from .render import render
            render(board, parts, out, title=script.parent.name)
        print(f"wrote {out}")
    return 0 if report["ok"] else 1


def slice_printed(mod, stls: dict) -> dict | None:
    """Each printed STL sliced, with the script's SLICE options (a dict
    value is one part's own); None when there is no slicer to be had."""
    opts = getattr(mod, "SLICE", {}) or {}
    common = {k: v for k, v in opts.items() if not isinstance(v, dict)}
    try:
        slicer.ensure(log=lambda m: print(m, file=sys.stderr))
    except Exception as e:
        print(f"slice: skipped, no slicer ({e}); `mech slice --install` to retry", file=sys.stderr)
        return None
    per = {n: {**common, **opts.get(n, {})} for n in stls}
    if all(v == common for v in per.values()):
        return slicer.slice_parts(stls, common)
    return {n: slicer.slice_parts({n: p}, per[n])[n] for n, p in stls.items()}


def cmd_slice(a) -> int:
    if a.install:
        print(slicer.ensure())
        if not a.stl:
            return 0
    if not a.stl:
        print("mech slice: give STL files (or --install)", file=sys.stderr)
        return 2
    sliced = slicer.slice_parts({Path(p).stem: Path(p) for p in a.stl})
    print(json.dumps(sliced, indent=1))
    print(slicer.summary(sliced), file=sys.stderr)
    return 0 if all("error" not in s for s in sliced.values()) else 1


def cmd_score(a) -> int:
    """mech's score (mech.corpus): enclosure faults planted on known-good
    boxes, 100 x (caught - false alarms) / planted. Higher is better.
    --product: the product's pass ratio instead (cmd_product)."""
    if a.product:
        return cmd_product(a)
    from . import corpus
    r = corpus.run()
    print(f"{corpus.score(r):.2f}")
    return 0


def cmd_product(a) -> int:
    """The product (examples/product, pinned by `mech pin`): every product
    enclosure built on its board and fit-checked, then every pinned board
    graded by the mean of fit checks passed / fit checks over the enclosures
    that hold it (else the module's stand-in examples/<name>/enclosure.py;
    one that doesn't build grades 0, a board nothing holds grades 0). Prints
    100 x the boards' mean grade. Higher is better."""
    from . import product
    meta = product.load_pin()
    if not meta or not meta.get("boards"):
        print("score: no pinned product boards (mech pin <clone>)", file=sys.stderr)
        return 1
    print(f"product: {meta['repo']} @ {meta['sha'][:12]}")
    jsons = {n: product.PRODUCT / "boards" / f"{n}.json" for n in meta["boards"]}

    def run(label, script, board):
        try:
            ok, n, bad, held = product.grade(script, jsons[board], jsons)
        except Exception as e:  # a broken enclosure costs its boards, not the score run
            print(f"{label}: does not build ({script.relative_to(ROOT)} on {board}): {e}")
            return 0.0, {board}
        print(f"{label}: {ok}/{n} ({script.relative_to(ROOT)}; holds {', '.join(sorted(held))})"
              + (f" failing: {', '.join(bad)}" if bad else ""))
        return (ok / n if n else 0.0), held

    by_board: dict[str, list[float]] = {n: [] for n in meta["boards"]}
    for name, e in (meta.get("enclosures") or {}).items():
        g, held = run(name, product.PRODUCT / e["script"], e["board"])
        for b in held:
            by_board[b].append(g)
    grades = []
    for name, gs in by_board.items():
        if not gs and (s := product.stand_in(name)):
            g, _ = run(f"{name} stand-in", s, name)
            gs = [g]
        g = sum(gs) / len(gs) if gs else 0.0
        print(f"board {name}: {100 * g:.1f}" + ("" if gs else " (no enclosure holds it)"))
        grades.append(g)
    print(f"{100.0 * sum(grades) / len(grades):.2f}")
    return 0


def cmd_pin(a) -> int:
    from .product import pin
    meta = pin(Path(a.clone), a.sha)
    print(f"pinned {meta['repo']} @ {meta['sha'][:12]}: {', '.join(meta['boards'])}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mech", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("board", help="read a .kicad_pcb into board.json")
    s.add_argument("pcb")
    s.add_argument("-o", "--output")
    s.add_argument("--heights", help="heights.json: per-ref or per-footprint overrides")
    for name in ("build", "fit"):
        s = sub.add_parser(name)
        s.add_argument("script")
        s.add_argument("--board")
        if name == "build":
            s.add_argument("-o", "--output")
            s.add_argument("--no-render", action="store_true")
            s.add_argument("--no-slice", action="store_true", help="skip PrusaSlicer")
    s = sub.add_parser("slice", help="slice STLs with PrusaSlicer: grams, support, print time")
    s.add_argument("stl", nargs="*")
    s.add_argument("--install", action="store_true", help="fetch the pinned PrusaSlicer first")
    s = sub.add_parser("score", help="planted enclosure faults caught (mech.corpus)")
    s.add_argument("--product", action="store_true", help="the pinned product's fit-check pass ratio instead")
    s = sub.add_parser("pin", help="pin the product's committed boards for the score")
    s.add_argument("clone", help="a hifi-receiver clone, e.g. $HIVE_PROJECTS/hifi-receiver-2")
    s.add_argument("--sha", default="HEAD")
    s = sub.add_parser("onshape", help="hand a STEP to Onshape (docs/onshape.md)")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd == "board":
        return cmd_board(a)
    if a.cmd == "build":
        return cmd_build(a)
    if a.cmd == "fit":
        return cmd_build(a, export=False)
    if a.cmd == "slice":
        return cmd_slice(a)
    if a.cmd == "score":
        return cmd_score(a)
    if a.cmd == "pin":
        return cmd_pin(a)
    if a.cmd == "onshape":
        from .onshape import main as om
        return om(a.rest)
    return 2


if __name__ == "__main__":
    sys.exit(main())
