"""Lite: lite-main in a two-part PETG box, built and fit-checked by hive-mech's `mech build`.

    mech build enclosure/lite.py -o enclosure/out/lite

It shares core_box.py's pieces with Plus and Ultra: the screws from below
through the base and the board into lid pillars, round RCA holes with a port
per barrel, binding posts on the rear wall, the light pipe. Everything is in
mech's board frame: mm, x right along the front edge, y back (the rear panel
is the N edge, y = 90), z up, z = 0 the board's bottom face.

The board is changed before mech's fit check sees it: the speaker terminal
(J4) is internal (the posts are wired to it), the RCA jacks are cut to their
body inside the rear wall, the IR receiver's lens is a port through the front
wall, and each binding post's nut and lug is a box the check knows.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mech.kicad import Port  # noqa: E402

from core_box import GAP, T_PCB, base, button, light_pipe, lid, post_ports, rca_ports, tray  # noqa: E402

BOARD = "lite-main.board.json"   # mech board ../boards/lite-main/kicad/lite-main.kicad_pcb --heights heights.json
PRINT = {"lid": "flip", "btn_SW1": "flip", "btn_SW2": "flip", "btn_SW3": "flip"}
FIT = {"max_recess": {"*": 3.0, "U9": 4.0}}   # U9 is seen through its window, not plugged: its lens 3.5 mm in

GAPS = {k: GAP["W"] for k in "WSEN"}      # 0.6 all round: nothing overhangs Lite's edges
HEADROOM = 36.0     # board top to lid underside: RCA 27 mm, then the binding posts' nuts above them
POST_X = (16.0, 35.0, 65.0, 84.0)         # 19 mm within a pair (dual banana plugs): L+ L-, R+ R- seen from behind
POST_Z = T_PCB + 31.5                     # over the RCA bodies
IR_WINDOW = (9.0, 8.0)                    # w x h round the IRM-3638T's lens
IR_LENS_Z = T_PCB + 5.0


def build(board):
    z_top = T_PCB + HEADROOM
    y_wall_n = board.size[1] + GAPS["N"]
    board.ports = [p for p in board.ports if p.ref == "J1"]   # USB-C; J4 is wired to the posts inside
    feats = {}
    rca_ports(board, ("J2", "J3"), feats, GAPS["N"])
    post_ports(board, POST_X, {"A": POST_Z}, y_wall_n, feats)
    # IR: the receiver looks forward out of a window in the front wall
    u = board.part("U9")
    board.ports.append(Port("U9", "S", [u.x - IR_WINDOW[0] / 2, u.x + IR_WINDOW[0] / 2,
                                        IR_LENS_Z - IR_WINDOW[1] / 2, IR_LENS_Z + IR_WINDOW[1] / 2],
                            u.bbox[1], 0.0, "top"))
    t = tray(board, "lite", GAPS, z_top)
    b = base(t, feats, ports=("J1", "U9"))
    # vents over and under the amplifier (U8) and its inductors. No label recess underneath: the base
    # prints on its floor, so a recess's roof is a 22 mm bridge off the bed; a sticker goes on flat.
    a = board.part("U8")
    vent = (a.x - 15, 39.0, a.x + 15, 65.0)
    b = t.vents(b, vent, along="y", where="floor")
    l = lid(t, feats)
    l = t.vents(l, vent, along="y")
    # status LED: a light pipe, pulled back from the front wall where the LED sits at the edge
    d2 = board.part("D2")
    od = 4.6
    dy = max(0.0, -GAPS["S"] + od / 2 + 0.1 - (d2.bbox[1] + d2.bbox[3]) / 2)
    l = light_pipe(t, l, "D2", od=od, idd=2.6, dy=dy)
    parts = {"base": b}
    for sw in ("SW1", "SW2", "SW3"):
        l, btn = button(t, l, sw)
        parts[f"btn_{sw}"] = btn
    parts["lid"] = l
    return parts

