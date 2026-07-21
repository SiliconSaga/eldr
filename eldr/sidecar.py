"""The thermal layer SH3D can't hold: assemblies, design conditions, infiltration."""
from __future__ import annotations
from dataclasses import dataclass
import math
import yaml


@dataclass(frozen=True)
class DesignConditions:
    indoor_heating_f: float
    outdoor_heating_99_f: float
    supply_air_rise_f: float

    @property
    def heating_delta_t(self) -> float:
        return self.indoor_heating_f - self.outdoor_heating_99_f


@dataclass(frozen=True)
class SideCar:
    assemblies: dict[str, float]
    design: DesignConditions
    infiltration_ach: float
    existing_tons: float | None = None   # current equipment nominal tonnage (Manual S check)


def _require(d: dict, key: str, ctx: str):
    if key not in d:
        raise ValueError(f"side-car missing required key '{ctx}.{key}'")
    return d[key]


def load_sidecar(path: str) -> SideCar:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    design = _require(raw, "design", "root")
    infil = _require(raw, "infiltration", "root")
    equipment = raw.get("equipment") or {}
    existing_tons = equipment.get("existing_tons")
    sc = SideCar(
        assemblies={k: float(v) for k, v in _require(raw, "assemblies", "root").items()},
        design=DesignConditions(
            indoor_heating_f=float(_require(design, "indoor_heating_f", "design")),
            outdoor_heating_99_f=float(_require(design, "outdoor_heating_99_f", "design")),
            supply_air_rise_f=float(_require(design, "supply_air_rise_f", "design")),
        ),
        infiltration_ach=float(_require(infil, "ach", "infiltration")),
        existing_tons=None if existing_tons is None else float(existing_tons),
    )
    _validate(sc)
    return sc


def _validate(sc: SideCar) -> None:
    numeric = {
        "design.indoor_heating_f": sc.design.indoor_heating_f,
        "design.outdoor_heating_99_f": sc.design.outdoor_heating_99_f,
        "design.supply_air_rise_f": sc.design.supply_air_rise_f,
        "infiltration.ach": sc.infiltration_ach,
    }
    numeric.update({f"assemblies.{k}": v for k, v in sc.assemblies.items()})
    for name, val in numeric.items():
        if not math.isfinite(val):
            raise ValueError(f"{name} must be a finite number (got {val!r})")
    if sc.design.supply_air_rise_f <= 0:
        raise ValueError("design.supply_air_rise_f must be > 0 (it sizes CFM)")
    if sc.design.heating_delta_t <= 0:
        raise ValueError("design: indoor_heating_f must exceed outdoor_heating_99_f")
    if sc.infiltration_ach < 0:
        raise ValueError("infiltration.ach must be >= 0")
    for name, u in sc.assemblies.items():
        if u < 0:
            raise ValueError(f"assemblies.{name}: U-value must be >= 0")
    if sc.existing_tons is not None:
        if not math.isfinite(sc.existing_tons) or sc.existing_tons <= 0:
            raise ValueError(
                f"equipment.existing_tons must be a finite number > 0 (got {sc.existing_tons!r})")
