import os
import resource
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The suite's own memory, measured and bounded (issue #19: one boolean in the
# fit check once held 6 GB, on a machine that went down in swap). The run
# prints its peak RSS and fails when it passes the ceiling; MECH_TEST_MB moves it.
# A watcher stops it the moment it crosses (issue #22: a branch without that
# fix ran test_product to 4.3 GB for 19 minutes and thrashed the Hive): a
# separate process, since OCP holds the GIL through a long boolean.

CEILING_MB = int(os.environ.get("MECH_TEST_MB", "800"))


def _peak_mb() -> tuple[int, int]:
    """(this process, its largest child) peak RSS in MB (ru_maxrss is KB on Linux)."""
    return (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024)


WATCH = r"""
import os, signal, sys, time
pid, cap = int(sys.argv[1]), int(sys.argv[2])
def tree():
    kids = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                kids.setdefault(int(open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()[1]), []).append(int(d))
            except (OSError, IndexError, ValueError):
                pass
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo += [k for k in kids.get(p, []) if k != os.getpid()]
    return out
def rss(p):
    try:
        return int(open(f"/proc/{p}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") >> 20
    except (OSError, IndexError, ValueError):
        return 0
while os.getppid() == pid:
    ps = tree()
    mb = max(rss(p) for p in ps)
    if mb > cap:
        sys.stderr.write(f"\nmech test memory: a process reached {mb} MB RSS, over the {cap} MB ceiling: "
                         "the run is stopped (MECH_TEST_MB moves it)\n")
        for p in reversed(ps):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        sys.exit(1)
    time.sleep(0.5)
"""


def pytest_configure(config):
    if os.path.isdir("/proc") and not hasattr(config, "workerinput"):
        config._mech_watch = subprocess.Popen([sys.executable, "-I", "-c", WATCH, str(os.getpid()), str(CEILING_MB)])


def pytest_unconfigure(config):
    w = getattr(config, "_mech_watch", None)
    if w is not None:
        w.kill()
        w.wait()


def pytest_terminal_summary(terminalreporter):
    own, child = _peak_mb()
    over = "  OVER: the run fails" if max(own, child) > CEILING_MB else ""
    terminalreporter.write_line(f"mech test memory: peak RSS {own} MB (largest child {child} MB), "
                                f"ceiling {CEILING_MB} MB{over}")


def pytest_sessionfinish(session, exitstatus):
    own, child = _peak_mb()
    if max(own, child) > CEILING_MB and exitstatus == 0:
        session.exitstatus = 1
