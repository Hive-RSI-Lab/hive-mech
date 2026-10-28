"""Every example builds and passes its fit check (the score's ground)."""
from pathlib import Path

import pytest

from conftest import ROOT
from mech import fit
from mech.cli import printed_parts, run_script

EXAMPLES = sorted((ROOT / "examples").glob("*/enclosure.py"))


@pytest.mark.parametrize("script", EXAMPLES, ids=[p.parent.name for p in EXAMPLES])
def test_example_fits(script: Path):
    mod, board, parts = run_script(script)
    r = fit.check(board, parts, printed=printed_parts(mod, parts), **getattr(mod, "FIT", {}))
    assert r["ok"], fit.summary(r)
