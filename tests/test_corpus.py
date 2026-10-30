"""mech score's corpus: every planted fault is caught by the check meant
for it on the tiny box, a fault class with no check is missed, and a check
failing on a clean base is a false alarm."""
from conftest import FIXTURES
from mech import corpus

BAD = f'''from mech.box import Tray
BOARD = "{FIXTURES / "tiny.kicad_pcb"}"
PRINT = {{"lid": "flip"}}
def build(board):
    t = Tray(board)
    return {{"base": t.base(), "lid": t.lid()}}   # J1's opening never cut
'''

OK = BAD.replace("t.base()", "t.cut_ports(t.base())").replace(
    "t.lid()}   # J1's opening never cut", "t.cut_top(t.lid(), 'SW1', d=6.0)}   # SW1 reached through a hole")


def test_every_fault_is_caught_on_the_tiny_box():
    r = corpus.run({"tiny": corpus.BASES["tiny"]})
    missed = [x["fault"] for x in r["results"] if not x["caught"]]
    assert missed == [] and r["false_alarms"] == 0
    assert r["faults"] >= 15 and corpus.score(r) == 100.0
    assert {x["fault"] for x in r["results"]} == set(corpus.FAULTS) - {"top_access_covered"}  # SW1 has a button


def test_a_class_with_no_check_is_missed_and_a_failing_clean_base_alarms(tmp_path):
    bad, ok = tmp_path / "bad.py", tmp_path / "ok.py"
    bad.write_text(BAD)
    ok.write_text(OK)
    faults = {k: corpus.FAULTS[k] for k in ("port_blocked", "top_access_covered")}
    r = corpus.run({"bad": bad, "ok": ok}, faults)
    got = {(x["base"], x["fault"]): x["caught"] for x in r["results"]}
    # bad: port_J1 fails clean (one false alarm), so closing it again isn't a catch either;
    # its lid has no hole over SW1 to seal. ok: SW1 sealed, and mech has no check for that.
    assert got == {("bad", "port_blocked"): False, ("ok", "port_blocked"): True, ("ok", "top_access_covered"): False}
    assert r["false_alarms"] == 1
    assert corpus.score(r) == 0.0
