"""mech build/fit apply a heights file when the script's BOARD is a .kicad_pcb (#44)."""
import json
import shutil

import pytest

from conftest import FIXTURES
from mech.cli import run_script

SCRIPT = '''
from mech.box import block
{extra}
def build(board):
    return {{"base": block(0, 0, -3, 5, 5, -1)}}
'''


def make(tmp_path, extra="", board="tiny.kicad_pcb", heights=None, name="heights.json"):
    shutil.copy(FIXTURES / "tiny.kicad_pcb", tmp_path / "tiny.kicad_pcb")
    if heights is not None:
        (tmp_path / name).write_text(json.dumps(heights))
    s = tmp_path / "enc.py"
    s.write_text(f'BOARD = "{board}"\n' + SCRIPT.format(extra=extra))
    return s


def c1(board):
    return board.part("C1").z[1]


def test_no_heights_file_reads_the_models(tmp_path):
    _, b, _ = run_script(make(tmp_path))
    assert c1(b) == pytest.approx(11.5 + 1.6, abs=0.5)


def test_heights_beside_the_script_apply(tmp_path):
    _, b, _ = run_script(make(tmp_path, heights={"C1": {"above": 20.0}}))
    assert c1(b) == pytest.approx(b.thickness + 20.0)


def test_board_named_heights_file_applies_too(tmp_path):
    _, b, _ = run_script(make(tmp_path, heights={"C1": {"above": 7.0}}, name="tiny.heights.json"))
    assert c1(b) == pytest.approx(b.thickness + 7.0)


def test_script_heights_and_cli_heights(tmp_path):
    s = make(tmp_path, extra='HEIGHTS = "h/c1.json"')
    (tmp_path / "h").mkdir()
    (tmp_path / "h" / "c1.json").write_text(json.dumps({"C1": {"above": 9.0}}))
    _, b, _ = run_script(s)
    assert c1(b) == pytest.approx(b.thickness + 9.0)
    (tmp_path / "cli.json").write_text(json.dumps({"C1": {"above": 3.0}}))
    _, b, _ = run_script(s, heights_arg=[str(tmp_path / "cli.json")])
    assert c1(b) == pytest.approx(b.thickness + 3.0)


def test_heights_none_turns_the_default_off(tmp_path):
    _, b, _ = run_script(make(tmp_path, extra="HEIGHTS = []", heights={"C1": {"above": 20.0}}))
    assert c1(b) == pytest.approx(11.5 + 1.6, abs=0.5)


def test_heights_for_a_board_json_is_an_error(tmp_path):
    from mech.kicad import read_board
    (tmp_path / "b.json").write_text(read_board(FIXTURES / "tiny.kicad_pcb").to_json())
    s = make(tmp_path, extra='HEIGHTS = "heights.json"', board="b.json", heights={"C1": {"above": 1.0}})
    with pytest.raises(SystemExit, match="read already"):
        run_script(s)
    # a heights.json lying beside a board.json script is left alone, as before
    s.write_text('BOARD = "b.json"\n' + SCRIPT.format(extra=""))
    _, b, _ = run_script(s)
    assert c1(b) == pytest.approx(11.5 + 1.6, abs=0.5)


def test_missing_heights_file_is_an_error(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        run_script(make(tmp_path, extra='HEIGHTS = "nope.json"'))
