"""The tests' tiny board (tests/fixtures/tiny.kicad_pcb: a USB-C port, a
switch, a through-hole cap) in the plain Tray: the smallest known-good box
in mech score's fault corpus (mech/corpus.py)."""
from mech.box import Tray

BOARD = "../../tests/fixtures/tiny.kicad_pcb"
PRINT = {"lid": "flip", "btn": "flip"}


def build(board):
    t = Tray(board)
    lid, btn = t.button(t.lid(), "SW1")
    return {"base": t.cut_ports(t.base()), "lid": lid, "btn": btn}
