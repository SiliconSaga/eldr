"""Wire the engine together: a house model + side-car -> heating (+ cooling) report."""
from __future__ import annotations
import argparse
from eldr import geometry, sidecar, loads, report, sizing


def run(home_path: str, sidecar_path: str) -> str:
    """Run the full pipeline: a Home.xml or .sh3d + side-car -> report."""
    env = geometry.extract_envelope(home_path)
    sc = sidecar.load_sidecar(sidecar_path)
    heating = loads.heating_load(env, sc)
    cooling = loads.cooling_load(env, sc) if sc.cooling is not None else None
    cooling_btuh = cooling.total_btuh if cooling is not None else None
    s = sizing.size_equipment(heating.total_btuh, sc, cooling_btuh=cooling_btuh)
    return report.render_heating(heating, sc, sizing=s, cooling=cooling)


def main(argv=None):
    """CLI entry point: parse args and print the report."""
    ap = argparse.ArgumentParser(prog="eldr", description="Eldr Manual J — heating + cooling loads.")
    ap.add_argument("home", help="path to a Sweet Home 3D Home.xml or a packed .sh3d")
    ap.add_argument("sidecar", help="path to the Eldr side-car YAML")
    args = ap.parse_args(argv)
    print(run(args.home, args.sidecar))


if __name__ == "__main__":
    main()
