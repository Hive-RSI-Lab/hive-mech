"""mech's commands (bin/mech runs these with the venv's python).

    mech board <file.kicad_pcb> [-o board.json] [--heights heights.json]
                                                 what the enclosure needs, as JSON
    mech build <enclosure.py> [-o DIR] [--no-render] [--board B]
                                                 run it, export, fit-check, render
    mech fit <enclosure.py> [--board B]          the fit check alone
    mech pin <clone> [--sha REV]                 pin the product's boards for the score
    mech score                                   the pinned product's boards, each boxed and checked
    mech onshape ...                             see docs/onshape.md

An enclosure script is plain Python with build123d:

    BOARD = "board.json"                # or a .kicad_pcb; relative to the script
    PRINT = {"lid": "flip"}             # optional: parts turned for the bed
    FIT = {"max_recess": 3.0}           # optional: fit.check's keyword args
    def build(board):                   # board: mech.kicad.Board
        return {"base": ..., "lid": ...}  # assembled, in the board's frame

`build` writes into DIR (default: out/ beside the script): <part>.stl as
printed, enclosure.step (the assembly with the board as a slab, for
Onshape or any CAD), board.json, fit.json, fit.md (the same report as a
table, one pass/fail row per check) and the renders.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

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
        (out / "fit.md").write_text(fit.markdown(report, script.parent.name), encoding="utf-8")
        if not a.no_render:
            from .render import render
            render(board, parts, out, title=script.parent.name)
        print(f"wrote {out}")
    return 0 if report["ok"] else 1


def cmd_score(a) -> int:
    """The product's boards (examples/product, pinned by `mech pin`), each
    graded by its enclosure's fit checks passed / fit checks; a board with
    no enclosure, or one that doesn't build, grades 0. Prints 100 x the
    mean grade. Higher is better."""
    from . import product
    meta = product.load_pin()
    if not meta or not meta.get("boards"):
        print("score: no pinned product boards (mech pin <clone>)", file=sys.stderr)
        return 1
    print(f"product: {meta['repo']} @ {meta['sha'][:12]}")
    grades = []
    for name, (script, whose) in product.enclosures(meta).items():
        g = 0.0
        if script is None:
            print(f"{name}: no enclosure (product or examples/{name}/enclosure.py)")
        else:
            try:
                ok, n, bad = product.grade(script, product.PRODUCT / "boards" / f"{name}.json")
                g = ok / n if n else 0.0
                print(f"{name}: {ok}/{n} ({whose} {script.relative_to(ROOT)})"
                      + (f" failing: {', '.join(bad)}" if bad else ""))
            except Exception as e:  # a broken enclosure costs its board, not the score run
                print(f"{name}: does not build ({whose} {script.relative_to(ROOT)}): {e}")
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
    sub.add_parser("score")
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
