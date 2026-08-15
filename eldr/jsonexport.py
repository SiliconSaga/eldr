"""Serialize a computed Analysis to a structured JSON dict — the machine-readable
twin of the report and overview.

Same numbers, same pipeline (`cli.analyze`), just as data instead of prose: for a UI,
a spreadsheet, the eventual SH3D plugin, or grounding an "ask the house" chatbot in
exact values rather than letting a model guess them.
"""
from __future__ import annotations
import json
from eldr import loads, sizing as sizing_mod, spaces as spaces_mod


def _space_factors(a) -> dict:
    """Every buffer space a surface faces, with the ΔT fractions the engine applied.

    `cooling_factor` comes from `loads.applied_cooling_factor`, NOT from
    `spaces.policy_for` + `spaces.cooling_factor`. The attic's summer temperature is
    substituted at load time, so the *declared* policy is not the applied one: on Refrhus
    the declaration exports 0.50 while the engine loaded the ceiling at 3.66.
    `heating_factor` genuinely is the declared policy's — only summer is substituted —
    which is exactly why the asymmetry is easy to miss.

    A side-car with no `cooling` block has no summer to resolve, so `cooling_factor` is
    null rather than a fabricated number.
    """
    sc, d = a.sc, a.sc.design
    indoor_w = d.indoor_heating_f
    outdoor_w = indoor_w - d.heating_delta_t
    return {
        name: {
            "heating_factor": spaces_mod.heating_factor(
                spaces_mod.policy_for(name, sc.spaces), indoor_w, outdoor_w),
            "cooling_factor": loads.applied_cooling_factor(name, sc),
        }
        for name in sorted({s.space for s in a.env.surfaces if s.space is not None})
    }


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
        # The level stack, as data. `levels` is keyed by the level's SH3D *name* exactly
        # as `Envelope.level_heights_ft` is, so two levels sharing a name collide into one
        # entry. That is a load-time error ONLY when the side-car has a `levels:` block
        # (an entry could not address one of them unambiguously); with no `levels:` block
        # the collision is silent and reaches this map. See the README's known limitations.
        "levels": {name: {"height_ft": h} for name, h in a.env.level_heights_ft.items()},
        "spaces": _space_factors(a),
        # Present even when empty, so a consumer can tell "no gaps" from "old export".
        # `category` is the surface category THAT gap resolved to, carried from the
        # resolver rather than re-derived from the envelope; it is null only when rooms
        # sharing a name resolved to different categories (see `geometry.Void`).
        "voids": {name: {"area_ft2": v.area_ft2, "category": v.category}
                  for name, v in a.env.voids.items()},
        # New in this cut: the whole-house horizontal split becomes inspectable without
        # re-deriving it. `space` is null for anything not facing a buffer space.
        "surfaces": [
            {"category": s.category, "area_ft2": s.area_ft2, "space": s.space}
            for s in a.env.surfaces
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
            # A component of sensible_btuh, exported beside it exactly as the heating block
            # exports its own infiltration term — the report itemises it, so the data must
            # too, or the two renderings disagree about what the load is made of.
            "infiltration_btuh": a.cooling.infiltration_btuh,
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
