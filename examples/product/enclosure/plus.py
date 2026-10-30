"""Plus: the core board (Plus population) in a two-part PETG box. See core_box.py and README.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core_box import build_plus  # noqa: E402

BOARD = "core.board.json"     # mech board ../boards/core/kicad/core.kicad_pcb --heights heights.json
PRINT = {"lid": "flip", "btn_SW2": "flip", "btn_SW3": "flip", "knob": "flip"}
# U18 is seen, not plugged: the riser in front of the board puts it 7 mm further in (README.md, Heat)
FIT = {"max_recess": {"*": 3.0, "U18": 11.0}}


def build(board):
    return build_plus(board)
