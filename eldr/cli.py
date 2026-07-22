"""Wire the engine together: a house model + side-car -> heating (+ cooling) report."""
from __future__ import annotations
import argparse
from dataclasses import replace
from eldr import geometry, sidecar, loads, report, sizing, climate, ductd, ductmodel


def _resolve_climate(sc: sidecar.SideCar, env: geometry.Envelope):
    """Fill any omitted outdoor design temps from the model's lat/long. Returns (sc, station)."""
    design, cooling, station = sc.design, sc.cooling, None

    def _station():
        nonlocal station
        if station is None:
            if env.latitude is None or env.longitude is None:
                raise ValueError("outdoor design temp omitted but the model has no lat/long "
                                 "— set it in the side-car or the SH3D compass")
            station = climate.nearest_station(env.latitude, env.longitude)
        return station

    if design.outdoor_heating_99_f is None:
        design = replace(design, outdoor_heating_99_f=_station().heating_99_f)
    if cooling is not None and cooling.outdoor_1_f is None:
        cooling = replace(cooling, outdoor_1_f=_station().cooling_1_f)
    return replace(sc, design=design, cooling=cooling), station


def run(home_path: str, sidecar_path: str) -> str:
    """Run the full pipeline: a Home.xml or .sh3d + side-car -> report."""
    env = geometry.extract_envelope(home_path)
    sc = sidecar.load_sidecar(sidecar_path)
    sc, station = _resolve_climate(sc, env)
    heating = loads.heating_load(env, sc)
    cooling = loads.cooling_load(env, sc) if sc.cooling is not None else None
    cooling_btuh = cooling.total_btuh if cooling is not None else None
    s = sizing.size_equipment(heating.total_btuh, sc, cooling_btuh=cooling_btuh)

    # Prefer model-derived per-room ducts (Manual J 1c) whenever the model has rooms;
    # otherwise fall back to the side-car's hand-listed runs (if any).
    ducts, duct_plan = None, None
    if any(r.conditioned for r in env.rooms):
        duct_plan = ductmodel.plan_ducts(env, sc)
        ducts = ductmodel.size_from_plan(duct_plan)
    elif sc.ducts is not None and sc.ducts.runs:
        ducts = ductd.size_ducts([(r.name, r.cfm) for r in sc.ducts.runs],
                                 friction_rate=sc.ducts.friction_rate)
    return report.render_heating(heating, sc, sizing=s, cooling=cooling, station=station,
                                 ducts=ducts, duct_plan=duct_plan)


def main(argv=None):
    """CLI entry point: parse args and print the report."""
    ap = argparse.ArgumentParser(prog="eldr", description="Eldr Manual J — heating + cooling loads.")
    ap.add_argument("home", help="path to a Sweet Home 3D Home.xml or a packed .sh3d")
    ap.add_argument("sidecar", help="path to the Eldr side-car YAML")
    args = ap.parse_args(argv)
    print(run(args.home, args.sidecar))


if __name__ == "__main__":
    main()
