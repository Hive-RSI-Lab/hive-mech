"""The hifi receiver's Lite board in a two-part PETG box: the module's proof.

The board (100 x 90 mm, four M3 holes at the corners) screws onto bosses in
the base. The rear (the board's N edge) has openings for both RCA pairs
(J2, J3), USB-C (J1) and the speaker terminal (J4). The front (S edge) has a
window for the IR receiver (U9); the three switches (SW1-3) get printed
buttons through the lid, and the status LED (D2) a hole over it.

board.json is the Lite board at hifi-receiver edbe4bd, read by `mech board`.
The product's own enclosure lives in the hifi-receiver repo; this one is
here so the module's test and score run on a real board.
"""
from mech.box import Tray

BOARD = "board.json"
PRINT = {"lid": "flip", "btn_SW1": "flip", "btn_SW2": "flip", "btn_SW3": "flip"}


def build(board):
    t = Tray(board, wall=2.0, standoff=5.0, gap=0.6, top_clear=2.0)
    base = t.cut_ports(t.base())
    base = t.window(base, "U9", "S")
    lid = t.lid()
    lid = t.cut_top(lid, "D2", d=3.0)
    parts = {"base": base}
    for sw in ("SW1", "SW2", "SW3"):
        lid, btn = t.button(lid, sw, d=5.0)
        parts[f"btn_{sw}"] = btn
    parts["lid"] = lid
    return parts
