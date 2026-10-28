# hive-mech

A Hive module for mechanical design. Agents use it to make parametric,
3D-printable enclosures in code, fitted to KiCad boards:

- read a board's outline, holes, part heights and edge connectors
  (`mech board`)
- build the enclosure in build123d with a few bricks (`mech.box`)
- export STL and STEP, render it, and check the fit (`mech build`)
- hand the STEP to Onshape once the person gives a key (`mech onshape`)

How agents use it: [docs/README.md](docs/README.md). Onshape:
[docs/onshape.md](docs/onshape.md). Proof:
[examples/hifi-lite](examples/hifi-lite), the hifi receiver's Lite board in
a two-part box that passes every fit check.

    bin/mech test     # the module's tests
    bin/mech score    # fit checks passed over examples/, higher is better
