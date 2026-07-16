"""Wire the engine together: Home.xml + side-car -> heating-load report."""
from __future__ import annotations
import argparse
from eldr import geometry, sidecar, loads, report


def run(home_xml_path: str, sidecar_path: str) -> str:
    env = geometry.extract_envelope(home_xml_path)
    sc = sidecar.load_sidecar(sidecar_path)
    result = loads.heating_load(env, sc)
    return report.render_heating(result, sc)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="eldr", description="Eldr Manual J — Phase 1 heating load.")
    ap.add_argument("home_xml", help="path to an exploded Sweet Home 3D Home.xml")
    ap.add_argument("sidecar", help="path to the Eldr side-car YAML")
    args = ap.parse_args(argv)
    print(run(args.home_xml, args.sidecar))


if __name__ == "__main__":
    main()
