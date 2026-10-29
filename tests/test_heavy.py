"""bin/mech runs its heavy commands (the suite, build, fit, score) under
`hive agent heavy`, so turns take turns at loading the machine (issue #5: on
10-28 hwde suites, freerouting and a mech build at once took a 20-core load
to 137). A fake hive and a fake venv python record what would run."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, args, *, heavy_op=True, held=False):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "log"
    help_ = '  heavy     --what "the test suite" -- cmd args' if heavy_op else "  progress"
    hive = bin_ / "hive"
    hive.write_text(f"""#!/bin/sh
[ "$2" = help ] && {{ echo '{help_}'; exit 0; }}
echo "hive $*" >>"{log}"
shift 2; while [ "$1" != -- ]; do shift; done; shift
exec "$@"
""")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    py = venv / "bin" / "python"
    py.write_text(f'#!/bin/sh\necho "python held=$MECH_HEAVY_HELD $*" >>"{log}"\n')
    for f in (hive, py):
        f.chmod(0o755)
    (venv / ".mech-lock").touch()
    env = {k: v for k, v in os.environ.items() if k != "MECH_HEAVY_HELD"}
    env.update(PATH=f"{bin_}:{env['PATH']}", MECH_VENV=str(venv))
    if held:
        env["MECH_HEAVY_HELD"] = "1"
    out = subprocess.run([str(ROOT / "bin" / "mech"), *args], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return log.read_text().splitlines()


@pytest.mark.parametrize("args,what", [
    (["build", "enc.py"], "mech build"),
    (["fit", "enc.py"], "mech fit"),
    (["score"], "mech score"),
    (["test", "-q"], "mech test (the module's pytest suite)"),
])
def test_heavy_commands_wait_for_a_lease(tmp_path, args, what):
    hive, py = _run(tmp_path, args)
    assert hive.startswith(f"hive agent heavy --what {what} -- "), hive
    assert py.startswith("python held=1 "), py


@pytest.mark.parametrize("args", [["board", "x.kicad_pcb"], ["python", "-c", "1"]])
def test_light_commands_run_bare(tmp_path, args):
    lines = _run(tmp_path, args)
    assert len(lines) == 1 and lines[0].startswith("python held= "), lines


def test_inside_a_lease_it_runs_bare(tmp_path):
    """A test that runs mech build doesn't ask again."""
    lines = _run(tmp_path, ["build", "enc.py"], held=True)
    assert len(lines) == 1 and lines[0].startswith("python held=1 "), lines


def test_a_hive_without_the_op_runs_it_bare(tmp_path):
    lines = _run(tmp_path, ["build", "enc.py"], heavy_op=False)
    assert len(lines) == 1 and lines[0].startswith("python held=1 "), lines
