"""Wire the engine together: a house model + side-car -> heating (+ cooling) report."""
from __future__ import annotations
import argparse
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class Analysis:
    """The full computed pipeline — shared by the report and the overview so both
    render from identical numbers."""
    env: geometry.Envelope
    sc: sidecar.SideCar
    station: climate.Station | None
    heating: loads.HeatingResult
    cooling: loads.CoolingResult | None
    sizing: sizing.SizingResult
    ducts: ductd.DuctResult | None
    duct_plan: ductmodel.DuctPlan | None


def analyze(home_path: str, sidecar_path: str) -> Analysis:
    """Run the whole pipeline: a Home.xml or .sh3d + side-car -> computed results."""
    sc = sidecar.load_sidecar(sidecar_path)
    env = geometry.extract_envelope(home_path, sc.wall_boundaries, sc.levels)
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
    return Analysis(env=env, sc=sc, station=station, heating=heating, cooling=cooling,
                    sizing=s, ducts=ducts, duct_plan=duct_plan)


def run(home_path: str, sidecar_path: str) -> str:
    """Run the pipeline and render the Markdown report."""
    a = analyze(home_path, sidecar_path)
    return report.render_heating(a.heating, a.sc, sizing=a.sizing, cooling=a.cooling,
                                 station=a.station, ducts=a.ducts, duct_plan=a.duct_plan,
                                 env=a.env)


def list_walls(home_path: str, sidecar_path: str | None = None) -> str:
    """Markdown table of every wall + its resolved boundary — to pick ids for tagging."""
    wall_boundaries = sidecar.load_sidecar(sidecar_path).wall_boundaries if sidecar_path else {}
    lines = ["# Eldr — walls (id → boundary)", "",
             "Copy an `id` into the side-car `walls:` block to override its boundary "
             "(exterior / ground / buffer / interior).", "",
             "| id | level | endpoints (ft) | length | boundary |",
             "|---|---|---|---:|---|"]
    for w in geometry.wall_inventory(home_path, wall_boundaries):
        src = f"**{w.boundary}** (tagged)" if w.tagged else w.boundary
        lines.append(f"| `{w.id}` | {w.level_name} | "
                     f"({w.x0_ft:.1f}, {w.y0_ft:.1f}) → ({w.x1_ft:.1f}, {w.y1_ft:.1f}) | "
                     f"{w.length_ft:.1f} ft | {src} |")
    return "\n".join(lines)


def main(argv=None):
    """CLI entry point: parse args and print the report (or the wall listing)."""
    ap = argparse.ArgumentParser(prog="eldr",
                                 description="Eldr Manual J — heating + cooling loads + ducts.")
    ap.add_argument("home", help="path to a Sweet Home 3D Home.xml or a packed .sh3d")
    ap.add_argument("sidecar", nargs="?", help="path to the Eldr side-car YAML (required for the report)")
    # the output modes are mutually exclusive — you get one document, not a mix.
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--walls", action="store_true",
                      help="list the model's walls + boundaries for hand-tagging, instead of the report")
    mode.add_argument("--overview", action="store_true",
                      help="render the full narrative demo overview instead of the terse report")
    mode.add_argument("--json", action="store_true", dest="as_json",
                      help="emit the analysis as structured JSON instead of the report")
    args = ap.parse_args(argv)
    if args.walls:
        print(list_walls(args.home, args.sidecar))
        return
    if args.sidecar is None:
        ap.error("a side-car is required — `eldr <home> <sidecar>` (for the report, --overview, "
                 "or --json); use --walls to inspect walls without one")
    if args.overview:
        from eldr import overview   # lazy: overview imports cli
        print(overview.render_overview(args.home, args.sidecar))
        return
    if args.as_json:
        from eldr import jsonexport
        print(jsonexport.render_json(analyze(args.home, args.sidecar)))
        return
    print(run(args.home, args.sidecar))


if __name__ == "__main__":
    main()
