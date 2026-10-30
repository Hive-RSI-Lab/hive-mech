"""The score's ground: the product's boards pinned from a clone, and each
graded by the product's enclosure, else the module's stand-in, else 0."""
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


@pytest.fixture
def clone(tmp_path):
    d = tmp_path / "hifi"
    for b in ("alpha", "beta"):
        (d / "boards" / b / "kicad").mkdir(parents=True)
        (d / "boards" / b / "kicad" / f"{b}.kicad_pcb").write_bytes((FIXTURES / "tiny.kicad_pcb").read_bytes())
    (d / "boards" / "notes").mkdir()
    (d / "boards" / "notes" / "x.kicad_pcb").write_text("not a board's own file")
    (d / "enclosure").mkdir()
    (d / "enclosure" / "heights.json").write_text('{"C1": {"above": 9.0}}')
    (d / "enclosure" / "alpha.py").write_text(SCRIPT.format(board="alpha"))
    (d / "enclosure" / "own_cad.py").write_text("def build(): pass  # not mech\n")
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "boards")
    return d


def test_pin_reads_committed_boards_and_finds_product_scripts(clone, tmp_path):
    (clone / "boards" / "alpha" / "kicad" / "alpha.kicad_pcb").write_text("uncommitted edit")
    out = tmp_path / "pin"
    meta = product.pin(clone, "HEAD", out)
    assert sorted(meta["boards"]) == ["alpha", "beta"]
    assert meta["boards"]["alpha"]["enclosure"] == "enclosure/alpha.py"
    assert meta["boards"]["beta"]["enclosure"] is None
    assert meta["boards"]["alpha"]["heights"] == ["enclosure/heights.json"]
    b = json.loads((out / "boards" / "alpha.json").read_text())
    assert b["source"].startswith("boards/alpha/kicad/alpha.kicad_pcb@")  # the committed file, no host path
    assert [p["z"] for p in b["parts"] if p["ref"] == "C1"] == [[1.6, 10.6]]
    assert (out / "enclosure" / "alpha.py").is_file() and json.loads((out / "pin.json").read_text())["sha"]


def test_product_script_wins_then_stand_in_then_none(clone, tmp_path):
    out = tmp_path / "pin"
    meta = product.pin(clone, "HEAD", out)
    examples = tmp_path / "examples"
    (examples / "alpha").mkdir(parents=True)
    (examples / "alpha" / "enclosure.py").write_text("x")
    encs = product.enclosures(meta, out, examples)
    assert encs["alpha"] == (out / "enclosure" / "alpha.py", "product")
    assert encs["beta"] == (None, None)
    (examples / "beta").mkdir()
    (examples / "beta" / "enclosure.py").write_text("x")
    assert product.enclosures(meta, out, examples)["beta"][1] == "stand-in"
    ok, n, bad = product.grade(out / "enclosure" / "alpha.py", out / "boards" / "alpha.json")
    assert ok == n and not bad


def test_script_board_prefers_board_then_path():
    names = ["lite", "lite-main", "core"]
    assert product.script_board("enclosure/x.py", 'BOARD = "../boards/lite-main/kicad/lite-main.kicad_pcb"', names) == "lite-main"
    assert product.script_board("enclosure/core/enclosure.py", "", names) == "core"
    assert product.script_board("enclosure/misc.py", "", names) is None
    assert product.is_mech_script("from mech.box import Tray\ndef build(board):\n  pass\n")
    assert not product.is_mech_script("import build123d\ndef build(board):\n  pass\n")


def test_every_pinned_board_has_a_fitting_enclosure():
    """What `mech score` grades: today's pin, every board boxed."""
    meta = product.load_pin()
    assert meta and meta["boards"]
    for name, (script, whose) in product.enclosures(meta).items():
        assert script is not None, f"{name}: no enclosure"
        ok, n, bad = product.grade(script, product.PRODUCT / "boards" / f"{name}.json")
        assert not bad, f"{name} ({whose}): {bad}"


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
