"""The score's ground: the product's boards and own enclosures pinned from a
clone, each board graded by the enclosures that hold it, else the module's
stand-in, else 0."""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import FIXTURES, ROOT
from mech import product
from mech.box import Tray

SCRIPT = '''from mech.box import Tray
BOARD = "../boards/{board}/kicad/{board}.kicad_pcb"
PRINT = {{"lid": "flip", "btn": "flip"}}
def build(board):
    t = Tray(board)
    lid, btn = t.button(t.lid(), "SW1")
    return {{"base": t.cut_ports(t.base()), "lid": lid, "btn": btn}}
'''


def git(d, *a):
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                   capture_output=True)


HELPER = '''import json
from pathlib import Path
from mech.box import Tray
from mech.kicad import load
HERE = Path(__file__).resolve().parent
def box(board):
    load(HERE / "gamma.board.json")   # holds gamma too, as Ultra holds ultra-front
    json.loads((HERE.parent / "boards/beta/parts/parts.json").read_text())
    t = Tray(board)
    return {"base": t.cut_ports(t.base()), "lid": t.lid()}
'''

VIA_HELPER = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared_box import box
BOARD = "beta.board.json"
PRINT = {"lid": "flip"}
def build(board):
    return box(board)
'''


@pytest.fixture
def clone(tmp_path):
    d = tmp_path / "hifi"
    for b in ("alpha", "beta"):
        (d / "boards" / b / "kicad").mkdir(parents=True)
        (d / "boards" / b / "kicad" / f"{b}.kicad_pcb").write_bytes((FIXTURES / "tiny.kicad_pcb").read_bytes())
    (d / "boards" / "beta" / "parts").mkdir()
    (d / "boards" / "beta" / "parts" / "parts.json").write_text('{"parts": []}')
    (d / "boards" / "notes").mkdir()
    (d / "boards" / "notes" / "x.kicad_pcb").write_text("not a board's own file")
    (d / "enclosure" / "out").mkdir(parents=True)
    (d / "enclosure" / "heights.json").write_text('{"C1": {"above": 9.0}}')
    (d / "enclosure" / "alpha.py").write_text(SCRIPT.format(board="alpha"))
    (d / "enclosure" / "own_cad.py").write_text("def build(): pass  # not mech\n")
    (d / "enclosure" / "out" / "old.py").write_text(SCRIPT.format(board="alpha"))
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "boards")
    return d


def add_helper_boxes(d):
    """beta boxed through a helper module, and gamma committed only as mech's read of it."""
    from mech.kicad import read_board
    (d / "enclosure" / "shared_box.py").write_text(HELPER)
    (d / "enclosure" / "beta_box.py").write_text(VIA_HELPER)
    (d / "enclosure" / "gamma.board.json").write_text(read_board(FIXTURES / "tiny.kicad_pcb").to_json())
    git(d, "add", "-A")
    git(d, "commit", "-qm", "helper boxes")


def test_pin_reads_committed_boards_and_finds_product_scripts(clone, tmp_path):
    (clone / "boards" / "alpha" / "kicad" / "alpha.kicad_pcb").write_text("uncommitted edit")
    out = tmp_path / "pin"
    meta = product.pin(clone, "HEAD", out)
    assert sorted(meta["boards"]) == ["alpha", "beta"]
    assert meta["enclosures"] == {"alpha": {"script": "enclosure/alpha.py", "board": "alpha"}}
    assert meta["boards"]["alpha"]["enclosures"] == ["alpha"] and meta["boards"]["beta"]["enclosures"] == []
    assert meta["boards"]["alpha"]["heights"] == ["enclosure/heights.json"]
    b = json.loads((out / "boards" / "alpha.json").read_text())
    assert b["source"].startswith("boards/alpha/kicad/alpha.kicad_pcb@")  # the committed file, no host path
    assert [p["z"] for p in b["parts"] if p["ref"] == "C1"] == [[1.6, 10.6]]
    assert (out / "enclosure" / "alpha.py").is_file() and json.loads((out / "pin.json").read_text())["sha"]
    assert not (out / "enclosure" / "out").exists()


def test_pin_takes_boards_read_only_and_scripts_through_helpers(clone, tmp_path):
    add_helper_boxes(clone)
    out = tmp_path / "pin"
    meta = product.pin(clone, "HEAD", out)
    assert sorted(meta["boards"]) == ["alpha", "beta", "gamma"]
    assert meta["enclosures"]["beta_box"] == {"script": "enclosure/beta_box.py", "board": "beta"}
    assert "shared_box" not in meta["enclosures"] and "own_cad" not in meta["enclosures"]
    assert meta["boards"]["gamma"]["read"] == "enclosure/gamma.board.json"
    assert json.loads((out / "boards" / "gamma.json").read_text())["source"].startswith("enclosure/gamma.board.json@")
    assert (out / "boards" / "beta" / "parts" / "parts.json").is_file()


def test_grade_names_the_boards_an_enclosure_holds(clone, tmp_path):
    add_helper_boxes(clone)
    out = tmp_path / "pin"
    meta = product.pin(clone, "HEAD", out)
    jsons = {n: out / "boards" / f"{n}.json" for n in meta["boards"]}
    ok, n, bad, held = product.grade(out / "enclosure" / "beta_box.py", jsons["beta"], jsons)
    assert ok == n and not bad and held == {"beta", "gamma"}
    ok, n, bad, held = product.grade(out / "enclosure" / "alpha.py", jsons["alpha"], jsons)
    assert ok == n and not bad and held == {"alpha"}


def test_stand_in_for_a_board_no_product_box_holds(tmp_path):
    assert product.stand_in("beta", tmp_path) is None
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "enclosure.py").write_text("x")
    assert product.stand_in("beta", tmp_path) == tmp_path / "beta" / "enclosure.py"


def test_script_board_prefers_board_then_path():
    names = ["lite", "lite-main", "core"]
    assert product.script_board("enclosure/x.py", 'BOARD = "../boards/lite-main/kicad/lite-main.kicad_pcb"', names) == "lite-main"
    assert product.script_board("enclosure/core/enclosure.py", "", names) == "core"
    assert product.script_board("enclosure/misc.py", "", names) is None
    assert product.is_mech_script("from mech.box import Tray\ndef build(board):\n  pass\n")
    assert not product.is_mech_script("import build123d\ndef build(board):\n  pass\n")
    via = "from core_box import build_plus\ndef build(board):\n  return build_plus(board)\n"
    assert not product.is_mech_script(via)
    assert product.is_mech_script(via, {"core_box": "from mech.kicad import load\n"})


def test_every_pinned_board_is_held_by_a_fitting_enclosure():
    """What `mech score` grades: today's pin, every product enclosure fits and every board is held."""
    PRINTING = ("overhang_", "bridge_", "wall_")
    meta = product.load_pin()
    assert meta and meta["boards"] and meta["enclosures"]
    jsons = {n: product.PRODUCT / "boards" / f"{n}.json" for n in meta["boards"]}
    held = set()
    for name, e in meta["enclosures"].items():
        ok, n, bad, h = product.grade(product.PRODUCT / e["script"], jsons[e["board"]], jsons)
        # Printing without support (overhang_, bridge_, wall_) is what the
        # product's enclosures are still working towards: the score counts it.
        assert not [b for b in bad if not b.startswith(PRINTING)], f"{name}: {bad}"
        held |= h
    for name in set(meta["boards"]) - held:
        script = product.stand_in(name)
        assert script, f"{name}: no enclosure holds it"
        assert not product.grade(script, jsons[name])[2], f"{name} (stand-in)"


def test_overhanging_connector_passes_the_wall_and_its_leads_stay_on_the_board():
    from mech import fit
    from mech.kicad import load
    b = load(ROOT / "examples" / "product" / "boards" / "core.json")
    t = Tray(b)
    j = b.port("J4")
    assert fit.part_box(b.part("J4")).bounding_box().max.Y > t.outer[3]
    cut = t.port_box(j)
    assert cut.bounding_box().min.Y < t.inner[3] and cut.bounding_box().max.Y > j.plane
    assert fit.lead_box(b.part("J4"), b.size).bounding_box().max.Y <= b.size[1] + 1e-6
