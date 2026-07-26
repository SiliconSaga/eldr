"""Serialize a computed Analysis to a structured JSON dict — the machine-readable
twin of the report and overview.

Same numbers, same pipeline (`cli.analyze`), just as data instead of prose: for a UI,
a spreadsheet, the eventual SH3D plugin, or grounding an "ask the house" chatbot in
exact values rather than letting a model guess them.
"""
from __future__ import annotations
import json
from eldr import loads, sizing as sizing_mod


def analysis_to_dict(a) -> dict:
    """Turn a cli.Analysis into a plain, JSON-serializable dict."""
    sc, d = a.sc, a.sc.design
    room_loads = a.duct_plan.room_loads if a.duct_plan is not None \
        else loads.per_room_loads(a.env, sc)

    out: dict = {
        "design": {
            "indoor_heating_f": d.indoor_heating_f,
            "outdoor_heating_99_f": d.outdoor_heating_99_f,
            "heating_delta_t_f": d.heating_delta_t,
            "supply_air_rise_f": d.supply_air_rise_f,
            "ground_temp_f": d.ground_temp_f,
        },
        "station": (None if a.station is None else {
            "name": a.station.name,
            "heating_99_f": a.station.heating_99_f,
            "cooling_1_f": a.station.cooling_1_f,
        }),
        "infiltration_ach": sc.infiltration_ach,
        "heating": {
            "total_btuh": a.heating.total_btuh,
            "conduction_btuh": a.heating.conduction_btuh,
            "infiltration_btuh": a.heating.infiltration_btuh,
            "cfm": a.heating.cfm,
            "by_category": dict(a.heating.by_category),
        },
        "cooling": None,
        "equipment_sizing": {
            "basis": a.sizing.basis,
            "design_load_btuh": a.sizing.load_tons / sizing_mod.TONS_PER_BTUH,
            "design_load_tons": a.sizing.load_tons,
            "recommended_tons": a.sizing.rec_tons,
            "recommended_oversize_pct": a.sizing.rec_oversize_pct,
            "next_size_tons": a.sizing.next_tons,
            "next_size_oversize_pct": a.sizing.next_oversize_pct,
            "existing_tons": a.sizing.existing_tons,
            "existing_oversize_pct": a.sizing.existing_oversize_pct,
            "verdict": a.sizing.verdict,
        },
        "rooms": [
            {
                "name": r.name,
                "level_id": r.level_id,
                "conditioned": r.conditioned,
                "heating_btuh": r.heating_btuh,
                "cooling_sensible_btuh": r.cooling_btuh,
                "design_cfm": r.cfm,
            }
            for r in room_loads
        ],
        "ducts": None,
    }

    if a.cooling is not None:
        c = sc.cooling
        out["cooling"] = {
            "indoor_f": c.indoor_f,
            "outdoor_1_f": c.outdoor_1_f,
            "cooling_delta_t_f": c.cooling_delta_t,
            "shgc": c.shgc,
            "occupants": c.occupants,
            "total_btuh": a.cooling.total_btuh,
            "sensible_btuh": a.cooling.sensible_btuh,
            "latent_btuh": a.cooling.latent_btuh,
            "cfm": a.cooling.cfm,
            "by_category": dict(a.cooling.by_category),
        }

    if a.ducts is not None:
        p = a.duct_plan
        out["ducts"] = {
            "friction_rate_inwc_per_100ft": a.ducts.friction_rate,
            "friction_derived": bool(p is not None and p.derived),
            "available_static_pressure_inwc": None if p is None else p.available_static_pressure,
            "unit_name": None if p is None else p.unit_name,
            "unit_placed": bool(p is not None and p.unit is not None),
            "worst_run_length_ft": None if p is None else p.worst_length_ft,
            "runs": [
                {
                    "name": r.name,
                    "cfm": r.cfm,
                    "exact_dia_in": r.exact_dia_in,
                    "standard_dia_in": r.standard_dia_in,
                    "velocity_fpm": r.velocity_fpm,
                    "flag": r.flag,
                    "length_ft": r.length_ft,
                    "pressure_drop_inwc": r.pressure_drop_inwc,
                }
                for r in a.ducts.runs
            ],
        }
    return out


def render_json(a, indent: int = 2) -> str:
    """Render a cli.Analysis as an indented JSON string."""
    return json.dumps(analysis_to_dict(a), indent=indent)
