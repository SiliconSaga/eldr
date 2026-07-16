# Eldr — Manual J heat-load engine (Phase 1a)

Read-only engine: parses an exploded Sweet Home 3D `Home.xml` + a YAML side-car
and prints a whole-house **heating load** + supply **CFM**. First slice of the
Eldr design (`realm-siliconsaga` `docs/plans/2026-07-15-eldr-manual-j-design.md`).

## Test

```bash
ws test eldr
```

(Runs the suite in the component's `.venv`; direct form: `.venv/bin/python -m pytest eldr/tests` from `components/eldr/`.)

## Run against a house

```bash
# from components/eldr/, using the bundled example side-car
.venv/bin/python -m eldr.cli ../../hoards/refrhus/sh3d-internals/Home.xml eldr/example-sidecar.yaml
```

Numbers are rough until the house model's schematic true-up + real assemblies —
that's expected for Phase 1a.

## Scope

Phase 1a is a whole-house heating skeleton. **Deferred to follow-up plans:**
cooling (solar-by-orientation + latent, 1b), per-room zoning + CFM (1c),
lat/long → design-station climate lookup, the interview skill, and the `.sh3p`
plugin. See the design doc for the full architecture and phasing.
