import os
import resource
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The suite's own memory, measured and bounded (issue #19: one boolean in the
# fit check once held 6 GB, on a machine that went down in swap). The run
# prints its peak RSS and fails when it passes the ceiling; MECH_TEST_MB moves it.

CEILING_MB = int(os.environ.get("MECH_TEST_MB", "800"))


def _peak_mb() -> tuple[int, int]:
    """(this process, its largest child) peak RSS in MB (ru_maxrss is KB on Linux)."""
    return (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024)


def pytest_terminal_summary(terminalreporter):
    own, child = _peak_mb()
    over = "  OVER: the run fails" if max(own, child) > CEILING_MB else ""
    terminalreporter.write_line(f"mech test memory: peak RSS {own} MB (largest child {child} MB), "
                                f"ceiling {CEILING_MB} MB{over}")


def pytest_sessionfinish(session, exitstatus):
    own, child = _peak_mb()
    if max(own, child) > CEILING_MB and exitstatus == 0:
        session.exitstatus = 1
