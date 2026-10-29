"""The hifi receiver's core board (Plus) in a two-part PETG box: the module's
stand-in until the product commits its own (`mech score` prefers that).

The board (120 x 100 mm, four M3 holes at the corners) screws onto bosses in
the base. The rear (N edge): the stacked RCA pairs J4 (SUB OUT) and J5 (LINE
IN) stand 7.3 mm past the board's edge and pass through the wall, the four
speaker terminals (J6, J7, J10, J11) take their wires through it, and the
optical input U19 gets a window. The left (W): USB-C power (J1) and Ethernet
(J2). The front (S): the IR receiver (U18) gets a window and the FFC (J3, for
Ultra's front board) a slot. Through the lid: the EC11 knob's bushing (SW1),
printed buttons for SW2/SW3, a hole over the LED (D3).

The board is examples/product/boards/core.json, pinned by `mech pin`.
"""
from mech.box import Tray

BOARD = "../product/boards/core.json"
PRINT = {"lid": "flip", "btn_SW2": "flip", "btn_SW3": "flip"}


def build(board):
    t = Tray(board, wall=2.0, standoff=5.0, gap=0.6, top_clear=2.0)
    base = t.cut_ports(t.base())
    base = t.window(base, "U18", "S")
    base = t.window(base, "U19", "N")
    base = t.pocket(base, "U7")  # the WROVER-IE overhangs the E edge 1.5 mm
    lid = t.lid()
    lid = t.cut_top(lid, "SW1", d=7.6)  # EC11's M7 bushing; the knob sits on the lid
    lid = t.cut_top(lid, "D3", d=3.0)
    parts = {"base": base}
    for sw in ("SW2", "SW3"):
        lid, btn = t.button(lid, sw, d=5.0)
        parts[f"btn_{sw}"] = btn
    parts["lid"] = lid
    return parts
