"""Ultra: the core board (Ultra population) and ultra-front behind a front panel. See core_box.py and README.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core_box import build_ultra  # noqa: E402

BOARD = "core.board.json"     # mech board ../boards/core/kicad/core.kicad_pcb --heights heights.json
# buttons stand on their flange's flat back, the cap up: cap-down, the flange's ring hangs in the air
PRINT = {"lid": "flip", "btn_FSW1": (-90, 0, 0), "btn_FSW2": (-90, 0, 0), "btn_FSW3": (-90, 0, 0),
         "knob": (-90, 0, 0)}
# LCD and F_U2 are seen, not plugged: mech measures their recess to the knob's front (the box's
# front-most point), not to the panel. Their real depths behind the panel face are in README.md.
FIT = {"max_recess": {"*": 3.0, "U19": 4.0, "LCD": 25.0, "F_U2": 25.0}}


def build(board):
    return build_ultra(board)
