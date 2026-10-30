"""The product mech is scored on: hifi-receiver's committed boards, pinned.

The score's sandbox sees only this checkout, so the product comes in as a
pin: `mech pin <clone> [--sha REV]` reads every committed board at REV
(boards/<name>/kicad/<name>.kicad_pcb, with the product's
enclosure/heights.json and enclosure/<name>.heights.json) into
examples/product/boards/<name>.json, copies the product's own mech
enclosure scripts (enclosure/**/*.py that define build() and use mech) into
examples/product/enclosure/, and writes examples/product/pin.json.

`mech score` then grades each pinned board by its enclosure: the product's
own script when the pin found one, else the module's stand-in
examples/<name>/enclosure.py, else none (0 for that board). A board's
grade is its fit checks passed over its fit checks; the score is 100 x
their mean, so a new board the pin brings in costs until it has a box that
fits. Bump the pin on purpose, in its own commit: it changes what is measured.
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


def is_mech_script(text: str) -> bool:
    return bool(re.search(r"^\s*def build\(", text, re.M)) and bool(re.search(r"^\s*(from|import) mech\b", text, re.M))


def script_board(rel: str, text: str, names: list[str]) -> str | None:
    """Which board a product script boxes: the longest board name in its BOARD, else in its path."""
    m = _BOARD.search(text)
    for hay in ([m.group(1)] if m else []) + [rel]:
        hits = [n for n in names if n in hay]
        if hits:
            return max(hits, key=len)
    return None


def pin(clone: Path, sha: str = "HEAD", out: Path = PRODUCT) -> dict:
    from .kicad import read_board, read_heights
    clone = Path(clone).resolve()
    sha = _git(clone, "rev-parse", f"{sha}^{{commit}}").strip()
    names = board_names(clone, sha)
    if not names:
        raise SystemExit(f"{clone}@{sha[:7]}: no boards/<name>/kicad/<name>.kicad_pcb")
    has_enc = bool(_git(clone, "ls-tree", "--name-only", sha, "--", "enclosure"))
    paths = [f"boards/{n}" for n in names] + (["enclosure"] if has_enc else [])
    tar = _git(clone, "archive", "--format=tar", sha, "--", *paths, binary=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with tarfile.open(fileobj=BytesIO(tar)) as tf:
            tf.extractall(tmp, filter="data")
        if out.exists():
            shutil.rmtree(out)
        (out / "boards").mkdir(parents=True)
        scripts = {}
        enc = tmp / "enclosure"
        for py in sorted(enc.rglob("*.py")) if enc.is_dir() else []:
            rel = py.relative_to(tmp).as_posix()
            if "/out/" in rel:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            if is_mech_script(text):
                b = script_board(rel, text, names)
                if b and b not in scripts:
                    scripts[b] = rel
        if scripts:  # the scripts with their helpers and data, not their outputs
            for f in enc.rglob("*"):
                rel = f.relative_to(tmp)
                if f.is_file() and "out" not in rel.parts and f.suffix in (".py", ".json"):
                    (out / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out / rel)
        boards = {}
        for n in names:
            heights, used = {}, []
            for h in ("enclosure/heights.json", f"enclosure/{n}.heights.json"):
                if (tmp / h).is_file():
                    heights.update(read_heights(tmp / h))
                    used.append(h)
            pcb = f"boards/{n}/kicad/{n}.kicad_pcb"
            b = read_board(tmp / pcb, heights=heights)
            b.source = f"{pcb}@{sha[:12]}"
            (out / "boards" / f"{n}.json").write_text(b.to_json() + "\n", encoding="utf-8")
            boards[n] = {"pcb": pcb, "heights": used, "enclosure": scripts.get(n),
                         "size": b.size, "parts": len(b.parts), "ports": [f"{p.ref}@{p.edge}" for p in b.ports]}
            print(f"{n}: {b.size[0]} x {b.size[1]} mm, {len(b.parts)} parts, ports "
                  f"{', '.join(boards[n]['ports']) or '-'}; enclosure: {scripts.get(n) or 'none in the product'}",
                  file=sys.stderr)
            for w in b.warnings:
                print(f"  warning: {w}", file=sys.stderr)
    meta = {"_doc": "hifi-receiver's committed boards at sha, read by `mech pin`; `mech score` grades an enclosure for each.",
            "project": "hifi-receiver-2", "repo": "Hive-RSI-Lab/hifi-receiver", "sha": sha, "boards": boards}
    (out / "pin.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def load_pin(root: Path = PRODUCT) -> dict | None:
    f = root / "pin.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def enclosures(meta: dict, root: Path = PRODUCT, examples: Path = ROOT / "examples") -> dict:
    """board -> (script, whose) for every pinned board; script None when it has none."""
    out = {}
    for n, b in meta["boards"].items():
        if b.get("enclosure") and (root / b["enclosure"]).is_file():
            out[n] = (root / b["enclosure"], "product")
        elif (examples / n / "enclosure.py").is_file():
            out[n] = (examples / n / "enclosure.py", "stand-in")
        else:
            out[n] = (None, None)
    return out


def grade(script: Path, board_json: Path) -> tuple[int, int, list[str]]:
    """(passed, checks, failing names) for one enclosure on one pinned board."""
    from . import fit
    from .cli import printed_parts, run_script
    mod, board, parts = run_script(script, str(board_json))
    r = fit.check(board, parts, printed=printed_parts(mod, parts), **getattr(mod, "FIT", {}))
    return sum(c["ok"] for c in r["checks"]), len(r["checks"]), [c["name"] for c in r["checks"] if not c["ok"]]
