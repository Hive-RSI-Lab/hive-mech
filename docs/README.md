# Making an enclosure with mech in the Hive

mech is for an agent that has to put a KiCad board in a 3D-printable box.
It reads the board, gives you a few build123d bricks, and checks the result.
The enclosure itself is a short Python script in the project you work in.
mech is a toolbox, not a pipeline: use the parts that help and write your own
build123d for the rest.

```
mech board boards/lite/kicad/lite-main.kicad_pcb -o enclosure/board.json
mech build enclosure/lite.py        # STL + STEP + fit check + renders in enclosure/out/
```

`mech` here means `$HIVE_MODULES/hive-mech/bin/mech` (the module's install
folder). It runs on the shared Python environment in the Hive's tools
folder (`/hive/tools/mech`, build123d with OCP, matplotlib, pytest, and
PrusaSlicer) when that pins the same `requirements.lock`. Otherwise, on
first use, it builds its own venv in `~/.cache/mech` with the Hive's uv.
That takes a few minutes, because OCP is a large download. A Hive check
starts with an empty HOME every time, so without the shared copy every
check would install OCP again. After a change to `requirements.lock`, a
warden runs `bin/mech publish /hive/tools` (the tools folder is read-only
to agents) so the shared copy pins the new lock; until then everyone
falls back to their own venv. Nothing gets installed outside your HOME,
and KiCad isn't needed: mech reads the `.kicad_pcb` file itself.

## 1. Read the board: `mech board`

`mech board <pcb> [-o board.json] [--heights heights.json]` writes
everything an enclosure needs, in one frame: mm, X right, Y up, origin at
the outline box's lower-left corner, z = 0 at the board's bottom face. It
also prints a one-line summary and a warning for every height it guessed.

- **outline**, **cutouts**: the Edge.Cuts loops. Arcs and circles are
  turned into segments.
- **holes**: mounting holes (MountingHole footprints, or H/MH refs with a
  hole of 2 mm or more), with their drill and pad size.
- **parts**: every footprint's courtyard box, side, z span and `under`
  (how far its leads stick out past the other face). `height_from` says
  where the height came from:
  - `model`: the 3D model the footprint names, VRML or STEP, with its
    offset, rotation and scale applied.
  - `name`: an `H<n>` in the footprint's name.
  - `table`: a common package.
  - `guess`: 5 mm, with a warning.
  - `override`: your heights file.
- **ports**: connectors within 2 mm of the outline box's edge, each with the
  `edge` (W E S N), the `face` rectangle, and a `kind` (`usb-c`,
  `usb-micro`, `usb-a`, `terminal`, `generic`). The kind sets the opening
  the mating plug needs (`Port.opening()`) and how deep the face may sit
  behind the outside (`kicad.PLUGS`).
- **top_access**: switches, LEDs, IR receivers and displays. Each one needs
  a decision: a button, a light pipe, a window, or nothing.

**Check the heights.** 3D models are often wrong. On the hifi Lite board,
the 470 µF cap's model is 7.8 mm tall but the real part is 13 mm, and the
EasyEDA STEP for the RCA jack sits 13.5 mm low. Put a datasheet number in a
`heights.json`:
`{"CONN-TH_XDJK-0271-1650": {"above": 27.0, "below": 2.9, "note": "..."}}`.
Keys can be a reference or a footprint name. This file is the same format
as hifi-receiver's `enclosure/heights.json`.

## 2. Write the enclosure

```python
from mech.box import Tray

BOARD = "board.json"                 # or the .kicad_pcb, relative to this file
PRINT = {"lid": "flip"}              # turn parts for the bed (or (rx, ry, rz))
FIT = {"max_recess": {"J4": 6.0}}    # optional: fit.check keyword args

def build(board):
    t = Tray(board, wall=2.0, standoff=5.0, gap=0.6, top_clear=2.0)
    base = t.cut_ports(t.base())     # floor, walls, bosses, every port's opening
    base = t.window(base, "U9", "S") # an IR receiver seen from the front
    lid = t.cut_top(t.lid(), "D2", d=3.0)
    lid, btn = t.button(lid, "SW1")  # a printed plunger for a tact switch
    lid = t.vents(lid, (55, 30, 95, 50))  # slots over the amplifier
    return {"base": base, "lid": lid, "btn_SW1": btn}
```

Something that isn't on the board but must fit inside the box (a heatsink,
a binding post's nut and lug, a second board and its parts) goes in as
`board.add_box(ref, x0, y0, z0, x1, y1, z1, what)` before you build round
it. The fit check then holds the plastic clear of it (`parts_clear`) and
it clear of the board's own parts (`added_parts_clear`). A part you make
yourself counts as added when its footprint starts with `enclosure:`
(`mech.kicad.ADDED`), as `add_box`'s do.

Return the parts **assembled**, in the board's frame; `mech build` turns
them for printing. `Tray` is one way to build the box: two parts, the board
screwed to bosses (pilot 2.5 mm for an M3 self-tapper in PETG, 4.0 for a
heat-set insert), walls that follow the board's outline, and a lid with a
locating lip that steps round tall parts.
`t.screws_from_below(base, lid)` swaps the self-tapper bosses for screws up
through the base and the board into pillars hanging from the lid, so four
screws clamp the board and hold the lid. `mech.box.teardrop(x, y, z, d,
length, axis)` is a horizontal hole with a 45° point on top, for a round
hole in a wall (a binding post, a round jack) that prints without support.
For a different shape (a sloped front, a panel-mount, a clamshell), use
`mech.box.block/rounded/cylinder` or plain build123d, and keep whatever
`Tray` helpers still fit. `examples/lite-main/enclosure.py` and
`examples/core/enclosure.py` are complete scripts. A part that overhangs
the board's edge (a module's antenna end) gets room with `t.pocket(base, ref)`;
a connector that does passes through the wall with `cut_ports` as it is.

## 3. Build and check: `mech build`

Inside a Hive turn, `build`, `fit`, `score` and `test` wait for `hive agent
heavy`'s lease (the machine is shared; at most 10 minutes, then they run
anyway), so give the shell command a timeout to match. Where the hive has no
`heavy` op yet they run at once.

`mech build <enclosure.py> [-o DIR] [--no-render] [--no-slice]` writes these to `out/`
next to the script:

- `<part>.stl`, each part as printed.
- `enclosure.step`, the assembly plus the board as a slab, for Onshape or
  any other CAD.
- `board.json`, `fit.json`, and `fit.md`: the same report as a table, one
  row per check whose second cell is `pass` or `fail`, for a project's own
  tests to read.
- `slice.json`: each printed part sliced by a real slicer (below), also a
  table at the end of `fit.md`.
- PNG renders.

It exits 1 if a fit check fails. `mech fit <enclosure.py>` runs the check
alone. The checks:

| check | what it means |
|---|---|
| board_clear | the board slab touches nothing |
| board_gap | nothing comes within 0.1 mm of the board's edge (`FIT = {"min_gap": ...}`): a wall that only touches it passes board_clear, but a printed box won't take the board |
| parts_apart | base, lid and buttons don't overlap as assembled |
| lid_covers | from above, the lid's outline and the base's wall tops cover the base's whole plan: no strip left open over the cavity (holes cut into the lid don't count, nor a fit gap under 1 mm round a lid that drops inside the walls). Only with a `base` and a `lid` part; `FIT = {"lid": ..., "base": ...}` names others |
| added_parts_clear | what the script added with `board.add_box` doesn't sit inside one of the board's own parts or its leads. Only when something was added |
| parts_clear | no part's box, and no through-hole lead under the board, runs into the enclosure |
| port_<ref> | the plug's opening is clear straight out through the wall, and the face isn't recessed deeper than the plug can reach |
| hole_<ref> | the board is held under each mounting hole |
| screw_<ref> | a screw can go through each mounting hole: the axis under the board is open 2 mm down (a pilot or clearance hole), or a peg fills the hole |
| bed_<part> | the part as printed fits the bed (default 220 x 220 x 250 mm; `FIT = {"bed": (...)}`) |
| overhang_<part> | turned as it prints (`PRINT`), no downward surface is steeper than 45 degrees from upright. A teardrop's 45-degree point passes; a horizontal round hole up to 7 mm across doesn't count |
| bridge_<part> | turned as it prints, every flat downward face (a port's top, a flange's underside) has wall going down from its edges: across a gap at most 15 mm (`max_bridge`), held from one side at most 1 mm out (`max_ledge`), and never from no side (it would start in mid-air) |
| wall_<part> | cut across every mm of height, no wall, rib or lip is thinner than two nozzle widths (0.8 mm for a 0.4 nozzle) |

The three print checks take `FIT = {"printing": {"nozzle": 0.6, "max_bridge": 20, ...}}`
(mech.printable.check's arguments). The `Tray` bricks print without
support as they come: an opening wider than `roof` (12 mm) gets 45-degree
shoulders over it, two openings or lip notches closer than `web` (1 mm) are
cut as one, a `pocket` inside the lid's outline runs up to the wall's top,
and a button's flange meets its cap on a 45-degree cone.

It also lists what is above each top-access part (open, lid, a button) and
each part's volume and solid PETG mass, which gives a material cost (PETG is
about $20/kg). The slicer's figures below are the real ones.

### What printing it costs: the slicer

`mech build` slices every printed STL with PrusaSlicer's CLI and reports,
per part and in total, the filament (grams, metres, cm3, cost at $25/kg),
the support material it had to add (`support_g`, `support_cm3`,
`support_share`) and the print time PrusaSlicer estimates. Support is
measured from the G-code itself (its support and support interface
extrusions), so `support_g = 0` means the part prints as it lies with none.
The geometry checks above are a fast guess; the slicer is the word: on
hifi-receiver's Lite enclosure every print check passes, and PrusaSlicer
still adds 3.5 g of support under the base and 0.2 g under each button.

The settings are `mech/slicer.ini`: the receiver's own (PETG, 0.4 mm nozzle,
0.2 mm layers, 4 perimeters, 20 % gyroid, auto support at 45 degrees) on an
MK4-sized machine's speeds and limits. An enclosure changes them with
PrusaSlicer's own option names, for every part or one:

```python
SLICE = {"fill_density": "15%", "lid": {"perimeters": 3}}
```

The slicer is PrusaSlicer 2.8.1 (its last Linux build), pinned by sha256 and
unpacked into `$MECH_HOME/prusaslicer-2.8.1` the first time it's needed
(about 100 MB to fetch, 120 MB kept; `mech slice --install` does it ahead).
`mech slice <part.stl ...>` slices STLs alone and prints the JSON.
`--no-slice` or `MECH_SLICE=0` skips it; with no network the build says it
skipped and goes on. A part takes from seconds to a minute.

The renders are hidden-line drawings. `iso.png` shows the assembly with the
lid lifted, `top.png` the top view, and `port-<edge>.png` one view per edge
with ports, where each connector (blue) should be framed by its opening.
Look at them: the checks only catch what they measure.

## Things to know

- Use PETG or ASA, not PLA, near anything warm (an amplifier's heatsink
  reaches 60-80 °C). Walls of 2 mm and a 5 mm standoff print well. Leave a
  0.25 mm gap on press fits.
- Port openings follow the plug, not the receptacle. A USB-C opening is at
  least 12.4 x 6.6 mm, so the overmold fits. A connector set back from the
  board edge adds that set-back to the recess. Tell the board's worker to
  move it to the edge rather than thinning the wall.
- The cavity follows the board's outline (Edge.Cuts) grown by `gap`, so a
  shaped board sits in a box of the same shape. Each wall, the lid and its lip
  are offsets of that plan. Convex corners round by the offset; concave ones
  stay sharp. `Tray(board, shape="box")` gives the old rounded rectangle round
  the outline's box, with `corner` as its radius. A `Tray` subclass that
  overrides `inner` (a gap per side) is a box by default, since the outline's
  plan can't follow it; asking it for `shape="outline"` is an error. The board's internal cutouts
  aren't in the plan. A part overhanging a shaped edge that isn't a port
  shows up in `parts_clear`.
- build123d's `Text()` can hang for minutes looking for fonts, so leave
  embossed labels out or give it a font file path.
- The machine is often loaded. One CAD build at a time, at `nice 19`, takes
  about 1 min for the Lite box.

## Handing it to the person: Onshape

See [onshape.md](onshape.md). In short: STEP goes to Onshape as a new
document, with the person's API keys in the environment for that one run and
never in a file. Until they give keys, hand them `out/enclosure.step` and
the STLs.

## Test and score

- `mech test` runs the module's tests: the KiCad reader on a hand-written
  board, every fit check made to fail the way it should, the Onshape client
  against a fake server, every example's fit, the slicer's G-code reading,
  and PrusaSlicer itself on parts with and without support (those skip
  where no slicer is installed; CI installs it and sets MECH_NEED_SLICER). It prints its peak RSS
  and fails over 800 MB (`MECH_TEST_MB` moves the ceiling); a watcher stops
  the run the moment any of its processes crosses it, rather than letting it
  swap. It peaks near 450 MB (test_product alone 470 MB, 30 s), most of it
  build123d/OCP itself.
- `mech score` measures mech itself: how many real enclosure mistakes its
  fit check catches (mech/corpus.py). The corpus is four known-good boxes
  (examples/tiny on the tests' board, the lite-main stand-in, and the
  product's own Lite and Plus as pinned), and about twenty faults planted
  on each in code at run time: a port closed or at the bottom of a deep
  collar, a standoff short or off its hole, the screw hole left out, the
  lid short, a wall tight on the board, a rib into the board, the lid or
  floor into a part, two parts in each other, an added heatsink on a part,
  a switch sealed under the lid, a part longer than the bed, and beside a
  printed part on the bed an overhang, a long bridge, a ledge, an island in
  the air and a thin fin. Each names the check meant to catch it; it is
  caught when that check fails planted and passed clean. Every check
  failing on a clean base is a false alarm. The score is 100 x (caught -
  false alarms) / planted: a fault class with no check yet (the sealed
  switch) is missed, so a new check raises it, and one that cries wolf
  lowers it. New faults and bases go in mech/corpus.py (FAULTS, BASES); a
  base must be good by review, not only by passing.
- `mech score --product` grades the real product: hifi-receiver's boards and its own
  enclosures, pinned in `examples/product/` (pin.json says the sha;
  boards/<name>.json is each committed KiCad board as `mech board` read it
  there, with the product's enclosure/heights.json, or the product's own
  enclosure/<name>.board.json for a board it commits only that way, as
  Ultra's ultra-front). Every product mech script (an `enclosure/**/*.py`
  that defines build() and imports mech, itself or through a helper next
  to it, as plus.py and ultra.py do through core_box.py) is built on the
  board its BOARD names and fit-checked; it holds that board and every
  other pinned board whose board.json it reads. A board's grade is the
  mean of fit checks passed / fit checks over the enclosures holding it
  (core: Plus and Ultra; ultra-front: Ultra), else the module's stand-in
  `examples/<name>/enclosure.py`, else 0. The score is 100 x the boards'
  mean. The score's sandbox sees only this checkout, hence the pin.
- `mech pin $HIVE_PROJECTS/hifi-receiver-2 [--sha REV]` re-pins (default:
  the clone's HEAD; pass `--sha main`). Bump it on purpose, in a commit of
  its own: a new board scores 0 until it has a box that fits, which is the
  point. After a bump, fix the stand-ins or the tools until it is back.

## Be critical of the tools

When a brick, a check or the reader gets something wrong, fix it here: a
false port, a height the table gets wrong, a check that passed a box that
didn't fit. A board-local workaround is the failure mode. If the fix isn't
small, file it with `hive agent brief --project hive-mech`.
