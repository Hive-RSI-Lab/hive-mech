"""A real slicer's word on each printed part: PrusaSlicer's CLI.

    slicer.slice_part(stl, overrides=None) -> dict
    slicer.slice_parts({name: stl}, overrides=None) -> {name: dict}

Each part is sliced with mech/slicer.ini (the hifi receiver's own print
settings: PETG, 0.4 mm nozzle, 0.2 mm layers, 4 walls, 20 % gyroid, auto
support at 45 degrees, a Prusa MK4-sized machine's speeds and limits) and
the G-code read back for what printing it costs:

- filament_g, filament_m, filament_cm3, cost (at filament_cost per kg):
  the slicer's own totals, support included.
- support_cm3, support_g: the filament the support material and its
  interface take (summed from the G-code's ;TYPE: sections). Zero means
  the part prints as it lies without any support.
- time_s, time: PrusaSlicer's estimate of the print, in seconds and as it
  writes it ("1h 2m 3s").
- by_type_g: grams per extrusion kind (perimeter, infill, support, ...).

The slicer is PrusaSlicer 2.8.1's AppImage (the last with a Linux build),
pinned by sha256, unpacked into $MECH_HOME/prusaslicer (`mech slicer
install`; about 120 MB after trimming). The GUI's web view libraries
(webkit2gtk, libsoup, javascriptcore) are linked but never called when
slicing, so where the system lacks them mech gives empty stand-ins of the
same name. MECH_SLICER names another prusa-slicer binary to use instead.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "2.8.1"
URL = ("https://github.com/prusa3d/PrusaSlicer/releases/download/version_2.8.1/"
       "PrusaSlicer-2.8.1%2Blinux-x64-newer-distros-GTK3-202409181416.AppImage")
SHA256 = "565f2f4bd4dbb05904a459d54db1916b6932124709c1d17b5aacfe9f5f2f1b03"
INI = Path(__file__).resolve().parent / "slicer.ini"
# The GUI's web view: linked, never called by the CLI.
STUBS = ("libwebkit2gtk-4.1.so.0", "libsoup-3.0.so.0", "libjavascriptcoregtk-4.1.so.0")
# Not needed to slice with our own profile: translations, vendor profiles, fonts, models.
TRIM = ("localization", "profiles", "fonts", "shapes")
SUPPORT_TYPES = ("Support material", "Support material interface")


def home() -> Path:
    base = os.environ.get("MECH_HOME") or str(Path.home() / ".cache" / "mech")
    return Path(base) / f"prusaslicer-{VERSION}"


def find() -> Path | None:
    """The prusa-slicer to run, or None when there is none yet."""
    env = os.environ.get("MECH_SLICER")
    if env:
        return Path(env)
    w = home() / "prusa-slicer"
    return w if w.exists() else None


def install(log=print) -> Path:
    """Download, check, unpack and trim the pinned AppImage into home();
    give stand-ins for the web view libraries the system lacks. Returns the
    wrapper to run."""
    dest = home()
    wrapper = dest / "prusa-slicer"
    if wrapper.exists():
        return wrapper
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="prusaslicer-", dir=dest.parent))
    try:
        app = tmp / "PrusaSlicer.AppImage"
        log(f"mech: fetching PrusaSlicer {VERSION} (about 100 MB, once)")
        h = hashlib.sha256()
        with urllib.request.urlopen(URL, timeout=120) as r, open(app, "wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != SHA256:
            raise RuntimeError(f"PrusaSlicer download: sha256 {h.hexdigest()}, want {SHA256}")
        app.chmod(0o755)
        subprocess.run([str(app), "--appimage-extract"], cwd=tmp, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        app.unlink()
        root = tmp / "squashfs-root"
        for d in TRIM:
            shutil.rmtree(root / "usr" / "bin" / "resources" / d, ignore_errors=True)
        binary = root / "usr" / "bin" / "bin" / "prusa-slicer"
        stubs = root / "mech-stubs"
        stubs.mkdir()
        missing = _missing_libs(binary, stubs)
        if missing:
            cc = shutil.which("cc") or shutil.which("gcc")
            if not cc:
                raise RuntimeError(f"PrusaSlicer needs {', '.join(missing)}; no C compiler to stand them in")
            src = stubs / "empty.c"
            src.write_text("\n")
            for lib in missing:
                subprocess.run([cc, "-shared", "-o", str(stubs / lib), f"-Wl,-soname,{lib}", str(src)],
                               check=True)
            still = _missing_libs(binary, stubs)
            if still:
                raise RuntimeError(f"PrusaSlicer needs libraries this system lacks: {', '.join(still)}")
        if dest.exists():
            shutil.rmtree(dest)
        root.rename(dest)
        w = dest / "prusa-slicer"
        w.write_text('#!/bin/sh\nhere=$(dirname "$0")\n'
                     'LD_LIBRARY_PATH="$here/mech-stubs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" '
                     'exec "$here/usr/bin/bin/prusa-slicer" "$@"\n')
        w.chmod(0o755)
        return w
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _missing_libs(binary: Path, stubs: Path) -> list[str]:
    env = dict(os.environ, LD_LIBRARY_PATH=str(stubs))
    r = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, env=env)
    return [ln.split()[0] for ln in r.stdout.splitlines() if "not found" in ln]


def ensure(log=print) -> Path:
    return find() or install(log)


def _ini_line(k, v) -> str:
    if isinstance(v, bool):
        v = int(v)
    return f"{k} = {v}"


def profile(overrides: dict | None = None) -> str:
    """slicer.ini with overrides in place (PrusaSlicer refuses a key twice)."""
    left = dict(overrides or {})
    lines = []
    for ln in INI.read_text(encoding="utf-8").splitlines():
        k = ln.partition("=")[0].strip()
        if "=" in ln and not ln.lstrip().startswith("#") and k in left:
            ln = _ini_line(k, left.pop(k))
        lines.append(ln)
    lines += [_ini_line(k, v) for k, v in left.items()]
    return "\n".join(lines) + "\n"


def slice_part(stl: Path, overrides: dict | None = None, gcode: Path | None = None,
               slicer: Path | None = None, timeout: float = 900) -> dict:
    """Slice one STL (already turned for the bed) and read the G-code back."""
    slicer = slicer or ensure()
    with tempfile.TemporaryDirectory(prefix="mech-slice-") as td:
        td = Path(td)
        cfg = td / "mech.ini"
        cfg.write_text(profile(overrides), encoding="utf-8")
        out = gcode or td / (Path(stl).stem + ".gcode")
        cmd = [str(slicer), "--datadir", str(td / "data"), "--load", str(cfg),
               "--export-gcode", "-o", str(out), str(stl)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, HOME=str(td)))
        if r.returncode != 0 or not out.exists():
            tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
            raise RuntimeError(f"prusa-slicer failed on {Path(stl).name}: " + " / ".join(tail))
        return parse_gcode(out.read_text(encoding="utf-8", errors="replace"))


def slice_parts(stls: dict, overrides: dict | None = None, jobs: int = 2,
                slicer: Path | None = None) -> dict:
    """{name: stl} -> {name: slice_part's dict, or {"error": ...}}."""
    slicer = slicer or ensure()

    def one(item):
        name, stl = item
        try:
            return name, slice_part(stl, overrides, slicer=slicer)
        except Exception as e:  # one part's failure is reported, not fatal to the build
            return name, {"error": str(e)}

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        return dict(ex.map(one, stls.items()))


_HMS = re.compile(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")


def parse_time(s: str) -> int:
    m = _HMS.fullmatch(s.strip())
    if not m or not any(m.groups()):
        raise ValueError(f"not a PrusaSlicer time: {s!r}")
    d, h, mi, se = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def parse_gcode(text: str) -> dict:
    """PrusaSlicer G-code (relative E, as slicer.ini sets) -> what it costs."""
    head = {}
    e_by_type: dict[str, float] = {}
    kind = None
    for ln in text.splitlines():
        if ln.startswith(";TYPE:"):
            kind = ln[6:].strip()
        elif ln.startswith("G1") and kind and " E" in ln:
            m = re.search(r"\sE(-?[\d.]+)", ln)
            if m:
                e_by_type[kind] = e_by_type.get(kind, 0.0) + float(m.group(1))
        elif ln.startswith("; ") and " = " in ln:
            k, _, v = ln[2:].partition(" = ")
            head.setdefault(k.strip(), v.strip())
    if head.get("use_relative_e_distances", "1") != "1":
        raise ValueError("mech reads relative-E G-code only (use_relative_e_distances = 1)")
    dia = float(head.get("filament_diameter", "1.75").split(",")[0])
    dens = float(head.get("filament_density", "1.27").split(",")[0])
    mm3_per_mm = math.pi * (dia / 2) ** 2

    def grams(e_mm):
        return e_mm * mm3_per_mm / 1000 * dens

    sup_mm = sum(v for k, v in e_by_type.items() if k in SUPPORT_TYPES)
    tkey = "estimated printing time (normal mode)"
    out = {
        "filament_g": float(head["total filament used [g]"]),
        "filament_m": round(float(head["filament used [mm]"].split(",")[0]) / 1000, 2),
        "filament_cm3": float(head["filament used [cm3]"].split(",")[0]),
        "cost": float(head.get("total filament cost", "0")),
        "support_cm3": round(sup_mm * mm3_per_mm / 1000, 2),
        "support_g": round(grams(sup_mm), 2),
        "time": head[tkey],
        "time_s": parse_time(head[tkey]),
        "by_type_g": {k.lower(): round(grams(v), 2) for k, v in sorted(e_by_type.items()) if v > 0},
    }
    out["support_share"] = round(out["support_g"] / out["filament_g"], 3) if out["filament_g"] else 0.0
    return out


def total(sliced: dict) -> dict:
    ok = [s for s in sliced.values() if "error" not in s]
    t = sum(s["time_s"] for s in ok)
    return {"filament_g": round(sum(s["filament_g"] for s in ok), 2),
            "support_g": round(sum(s["support_g"] for s in ok), 2),
            "support_cm3": round(sum(s["support_cm3"] for s in ok), 2),
            "cost": round(sum(s["cost"] for s in ok), 2),
            "time_s": t, "time": fmt_time(t), "parts": len(ok),
            "failed": sorted(n for n, s in sliced.items() if "error" in s)}


def fmt_time(s: int) -> str:
    h, rest = divmod(int(s), 3600)
    return (f"{h}h " if h else "") + f"{rest // 60}m"


def markdown(sliced: dict) -> str:
    lines = ["", f"## Print (PrusaSlicer {VERSION}, mech/slicer.ini)", "",
             "| part | filament g | support g | support cm3 | time | cost |", "|---|---|---|---|---|---|"]
    for n, s in sliced.items():
        if "error" in s:
            lines.append(f"| {n} | did not slice: {s['error'][:120]} | | | | |")
        else:
            lines.append(f"| {n} | {s['filament_g']} | {s['support_g']} | {s['support_cm3']} | {s['time']} | {s['cost']} |")
    t = total(sliced)
    lines.append(f"| all | {t['filament_g']} | {t['support_g']} | {t['support_cm3']} | {t['time']} | {t['cost']} |")
    return "\n".join(lines) + "\n"


def summary(sliced: dict) -> str:
    t = total(sliced)
    parts = ", ".join(f"{n} {s['filament_g']} g/{s['time']}" + (f" (support {s['support_g']} g)" if s["support_g"] else "")
                      for n, s in sliced.items() if "error" not in s)
    bad = f"; did not slice: {', '.join(t['failed'])}" if t["failed"] else ""
    return (f"slice: {t['filament_g']} g PETG, support {t['support_g']} g ({t['support_cm3']} cm3), "
            f"{t['time']} printing, {t['cost']} at filament cost: {parts}{bad}")
