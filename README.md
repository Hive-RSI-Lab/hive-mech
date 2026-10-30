# hive-mech

A Hive module for mechanical design. Agents use it to make parametric,
3D-printable enclosures in code, fitted to KiCad boards:

- read a board's outline, holes, part heights and edge connectors
  (`mech board`)
- build the enclosure in build123d with a few bricks (`mech.box`)
- export STL and STEP, render it, and check the fit (`mech build`)
- hand the STEP to Onshape once the person gives a key (`mech onshape`)

How agents use it: [docs/README.md](docs/README.md). Onshape:
[docs/onshape.md](docs/onshape.md). Proof: the hifi receiver's own
enclosures (Lite, Plus, Ultra), pinned with its boards (lite-main, core,
ultra-front) in [examples/product](examples/product), each box passing
every fit check on the board it holds; the module's stand-ins
([examples/lite-main](examples/lite-main), [examples/core](examples/core))
box a board the product has no enclosure for.

    bin/mech test     # the module's tests
    bin/mech score    # every pinned product board, graded by the enclosures that hold it; higher is better
