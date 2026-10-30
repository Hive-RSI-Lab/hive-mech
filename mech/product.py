"""The product mech is scored on: hifi-receiver's boards and its own enclosures, pinned.

The score's sandbox sees only this checkout, so the product comes in as a
pin: `mech pin <clone> [--sha REV]` reads every board at REV into
examples/product/boards/<name>.json: each committed KiCad board
(boards/<name>/kicad/<name>.kicad_pcb, with the product's
enclosure/heights.json and enclosure/<name>.heights.json), and each board
the product only commits as a `mech board` read (enclosure/<name>.board.json,
as Ultra's ultra-front). It copies the product's mech enclosure scripts with
their helpers and data (enclosure/**/*.py and *.json, boards/*/parts/*.json)
into examples/product/ and writes examples/product/pin.json, whose
"enclosures" names each script and the board its BOARD boxes.

`mech score` builds every product enclosure on its pinned board and records
which pinned boards it holds: its own, and any other whose board.json it
reads (Ultra holds ultra-front behind its front panel). A board's grade is
the mean, over the enclosures holding it, of fit checks passed / fit checks;
a board none holds is graded by the module's stand-in
examples/<name>/enclosure.py, else 0. The score is 100 x the boards' mean,
so a new board the pin brings in costs until a box holds it and fits. Bump
the pin on purpose, in its own commit: it changes what is measured.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

from .cli import ROOT

PRODUCT = ROOT / "examples" / "product"
_BOARD = re.compile(r"^\s*BOARD\s*=\s*[\"']([^\"']+)[\"']", re.M)


def _git(clone: Path, *args, binary=False):
    r = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(clone), *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()


def board_names(clone: Path, sha: str) -> list[str]:
    """Boards committed at sha: boards/<name>/kicad/<name>.kicad_pcb."""
    names = []
    for line in _git(clone, "ls-tree", "-r", "--name-only", sha, "--", "boards/").splitlines():
        parts = line.split("/")
        if len(parts) == 4 and parts[2] == "kicad" and parts[3] == f"{parts[1]}.kicad_pcb":
            names.append(parts[1])
    return sorted(names)


_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.M)


def is_mech_script(text: str, helpers: dict[str, str] | None = None) -> bool:
    """Defines build() and imports mech, itself or through a helper module next to it
    (helpers: module name -> its text), as hifi-receiver's plus.py does through core_box."""
    if not re.search(r"^\s*def build\(", text, re.M):
        return False
    helpers, seen, todo = helpers or {}, set(), [text]
    while todo:
        for mod in _IMPORT.findall(todo.pop()):
            if mod == "mech":
                return True
            if mod in helpers and mod not in seen:
                seen.add(mod)
                todo.append(helpers[mod])
    return False


def script_board(rel: str, text: str, names: list[str]) -> str | None:
    """Which board a product script boxes: the longest board name in its BOARD, else in its path."""
    m = _BOARD.search(text)
    for hay in ([m.group(1)] if m else []) + [rel]:
        hits = [n for n in names if n in hay]
        if hits:
            return max(hits, key=len)
    return None


def pin(clone: Path, sha: str = "HEAD", out: Path = PRODUCT) -> dict:
    from .kicad import load, read_board, read_heights
    clone = Path(clone).resolve()
    sha = _git(clone, "rev-parse", f"{sha}^{{commit}}").strip()
    pcbs = board_names(clone, sha)
    has_enc = bool(_git(clone, "ls-tree", "--name-only", sha, "--", "enclosure"))
    has_boards = bool(_git(clone, "ls-tree", "--name-only", sha, "--", "boards"))
    paths = (["boards"] if has_boards else []) + (["enclosure"] if has_enc else [])
    if not paths:
        raise SystemExit(f"{clone}@{sha[:7]}: no boards/ or enclosure/")
    tar = _git(clone, "archive", "--format=tar", sha, "--", *paths, binary=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with tarfile.open(fileobj=BytesIO(tar)) as tf:
            tf.extractall(tmp, filter="data")
        enc = tmp / "enclosure"
        reads = sorted(f.name[:-len(".board.json")] for f in enc.glob("*.board.json")) if enc.is_dir() else []
        names = sorted(set(pcbs) | set(reads))
        if not names:
            raise SystemExit(f"{clone}@{sha[:7]}: no boards/<name>/kicad/<name>.kicad_pcb or enclosure/<name>.board.json")
        if out.exists():
            shutil.rmtree(out)
        (out / "boards").mkdir(parents=True)
        pys = [] if not enc.is_dir() else [py for py in sorted(enc.rglob("*.py"))
                                           if "out" not in py.relative_to(enc).parts]
        helpers = {py.stem: py.read_text(encoding="utf-8", errors="replace") for py in pys}
        scripts = {}
        for py in pys:
            rel = py.relative_to(tmp).as_posix()
            text = helpers[py.stem] if py.parent == enc else py.read_text(encoding="utf-8", errors="replace")
            if is_mech_script(text, helpers):
                b = script_board(rel, text, names)
                if b:
                    scripts[py.stem if py.stem not in scripts else rel] = {"script": rel, "board": b}
        if scripts:  # the scripts with their helpers and data, not their outputs
            data = [f for f in enc.rglob("*") if "out" not in f.relative_to(enc).parts and f.suffix in (".py", ".json")]
            data += sorted(tmp.glob("boards/*/parts/*.json"))
            for f in data:
                if f.is_file():
                    rel = f.relative_to(tmp)
                    (out / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out / rel)
        boards = {}
        for n in names:
            heights, used = {}, []
            if n in pcbs:
                for h in ("enclosure/heights.json", f"enclosure/{n}.heights.json"):
                    if (tmp / h).is_file():
                        heights.update(read_heights(tmp / h))
                        used.append(h)
                src = f"boards/{n}/kicad/{n}.kicad_pcb"
                b = read_board(tmp / src, heights=heights)
            else:  # the product commits only mech's read of it
                src = f"enclosure/{n}.board.json"
                b = load(tmp / src)
            b.source = f"{src}@{sha[:12]}"
            (out / "boards" / f"{n}.json").write_text(b.to_json() + "\n", encoding="utf-8")
            held = sorted(e for e, s in scripts.items() if s["board"] == n)
            boards[n] = {"pcb" if n in pcbs else "read": src, "heights": used, "enclosures": held,
                         "size": b.size, "parts": len(b.parts), "ports": [f"{p.ref}@{p.edge}" for p in b.ports]}
            print(f"{n}: {b.size[0]} x {b.size[1]} mm, {len(b.parts)} parts, ports "
                  f"{', '.join(boards[n]['ports']) or '-'}; from {src}; boxed by: {', '.join(held) or '-'}",
                  file=sys.stderr)
            for w in b.warnings:
                print(f"  warning: {w}", file=sys.stderr)
    meta = {"_doc": "hifi-receiver's boards and its own mech enclosures at sha, read by `mech pin`; "
                    "`mech score` grades every board by the enclosures that hold it.",
            "project": "hifi-receiver-2", "repo": "Hive-RSI-Lab/hifi-receiver", "sha": sha,
            "boards": boards, "enclosures": scripts}
    (out / "pin.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def load_pin(root: Path = PRODUCT) -> dict | None:
    f = root / "pin.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def stand_in(name: str, examples: Path = ROOT / "examples") -> Path | None:
    """The module's own box for a board no product enclosure holds."""
    f = examples / name / "enclosure.py"
    return f if f.is_file() else None


def grade(script: Path, board_json: Path, boards: dict[str, Path] | None = None) -> tuple[int, int, list[str], set[str]]:
    """(passed, checks, failing names, boards held) for one enclosure on one pinned board.
    Held: board_json's board, and every other of `boards` (name -> pinned json) whose
    <name>.board.json or pinned json the build reads."""
    from . import fit
    from .cli import printed_parts, run_script
    opened: list[str] = []
    _OPENS.append(opened)
    try:
        mod, board, parts = run_script(script, str(board_json))
        r = fit.check(board, parts, printed=printed_parts(mod, parts), **getattr(mod, "FIT", {}))
    finally:
        _OPENS.remove(opened)
    read = {Path(o).name for o in opened}
    own = Path(board_json).resolve()
    held = {n for n, j in (boards or {}).items() if Path(j).resolve() == own or f"{n}.board.json" in read}
    return sum(c["ok"] for c in r["checks"]), len(r["checks"]), [c["name"] for c in r["checks"] if not c["ok"]], held


_OPENS: list[list[str]] = []   # grade()'s open() logs, fed by one audit hook


def _audit(event, args):
    if event == "open" and _OPENS and isinstance(args[0], (str, Path)):
        for log in _OPENS:
            log.append(str(args[0]))


sys.addaudithook(_audit)
