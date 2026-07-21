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
class Cooling:
    indoor_f: float
    outdoor_1_f: float          # 1% cooling design temp
    shgc: float                 # window solar heat gain coefficient (0..1)
    occupants: float            # for internal + latent gains

    @property
    def cooling_delta_t(self) -> float:
        return self.outdoor_1_f - self.indoor_f


@dataclass(frozen=True)
class SideCar:
    assemblies: dict[str, float]
    design: DesignConditions
    infiltration_ach: float
    existing_tons: float | None = None   # current equipment nominal tonnage (Manual S check)
    cooling: Cooling | None = None       # optional cooling design conditions (Manual J 1b)


def _require(d: dict, key: str, ctx: str):
    if key not in d:
        raise ValueError(f"side-car missing required key '{ctx}.{key}'")
    return d[key]


def _require_number(d: dict, key: str, ctx: str) -> float:
    """Require a key and coerce to float, rejecting booleans (bool is an int subtype)."""
    v = _require(d, key, ctx)
    if isinstance(v, bool):
        raise ValueError(f"{ctx}.{key} must be a number, not a boolean")
    try:
        return float(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{ctx}.{key} must be a number") from exc


def load_sidecar(path: str) -> SideCar:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    design = _require(raw, "design", "root")
    infil = _require(raw, "infiltration", "root")
    equipment = raw.get("equipment")
    if equipment is None:
        equipment = {}
    elif not isinstance(equipment, dict):
        raise ValueError("equipment must be a mapping")
    existing_tons = equipment.get("existing_tons")
    if isinstance(existing_tons, bool):   # bool is an int subtype -> float(True)==1.0
        raise ValueError("equipment.existing_tons must be a finite number > 0 (got a boolean)")
    cooling_raw = raw.get("cooling")
    if cooling_raw is not None and not isinstance(cooling_raw, dict):
        raise ValueError("cooling must be a mapping")
    cooling = None
    if cooling_raw is not None:   # an explicit `cooling: {}` must fail on missing keys
        cooling = Cooling(
            indoor_f=_require_number(cooling_raw, "indoor_f", "cooling"),
            outdoor_1_f=_require_number(cooling_raw, "outdoor_1_f", "cooling"),
            shgc=_require_number(cooling_raw, "shgc", "cooling"),
            occupants=_require_number(cooling_raw, "occupants", "cooling"),
        )
    sc = SideCar(
        assemblies={k: float(v) for k, v in _require(raw, "assemblies", "root").items()},
        design=DesignConditions(
            indoor_heating_f=float(_require(design, "indoor_heating_f", "design")),
            outdoor_heating_99_f=float(_require(design, "outdoor_heating_99_f", "design")),
            supply_air_rise_f=float(_require(design, "supply_air_rise_f", "design")),
        ),
        infiltration_ach=float(_require(infil, "ach", "infiltration")),
        existing_tons=None if existing_tons is None else float(existing_tons),
        cooling=cooling,
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
    if sc.cooling is not None:
        c = sc.cooling
        cnum = {"cooling.indoor_f": c.indoor_f, "cooling.outdoor_1_f": c.outdoor_1_f,
                "cooling.shgc": c.shgc, "cooling.occupants": c.occupants}
        for name, val in cnum.items():
            if not math.isfinite(val):
                raise ValueError(f"{name} must be a finite number (got {val!r})")
        if c.cooling_delta_t <= 0:
            raise ValueError("cooling: outdoor_1_f must exceed indoor_f")
        if not 0.0 <= c.shgc <= 1.0:
            raise ValueError("cooling.shgc must be between 0 and 1")
        if c.occupants < 0:
            raise ValueError("cooling.occupants must be >= 0")
