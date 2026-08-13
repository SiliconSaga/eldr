"""The thermal layer SH3D can't hold: assemblies, design conditions, infiltration."""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import yaml
from eldr import ductd, spaces as spaces_mod

# Deep soil temperature (°F) a below-grade surface is coupled to when the side-car
# doesn't specify one. Roughly the annual-mean air temp for a temperate US climate.
DEFAULT_GROUND_TEMP_F = 50.0

# Explicit per-wall boundary conditions the side-car may assign (by SH3D wall id).
# `buffer` (garage/crawl-adjacent) can only come from a tag — geometry can't infer it.
WALL_BOUNDARIES = frozenset({"exterior", "ground", "buffer", "interior"})

# Roles a level may be assigned. Deliberately NO `buffer`: buffer-ness belongs to a
# space's temperature policy (see spaces.py), not to a level. An unconditioned level
# simply becomes a named space; how it is treated thermally is decided in `spaces:`.
LEVEL_ROLES = frozenset({"conditioned", "unconditioned", "ignore"})


@dataclass(frozen=True)
class LevelSpec:
    role: str | None = None
    height_ft: float | None = None
    below_void: str | None = None
    above_void: str | None = None


@dataclass(frozen=True)
class DesignConditions:
    indoor_heating_f: float
    outdoor_heating_99_f: float | None   # None -> resolved from lat/long (see climate)
    supply_air_rise_f: float
    ground_temp_f: float = DEFAULT_GROUND_TEMP_F   # deep-soil temp for below-grade surfaces

    @property
    def heating_delta_t(self) -> float:
        if self.outdoor_heating_99_f is None:
            raise ValueError("outdoor_heating_99_f is unresolved — set it in the side-car "
                             "or provide the model's lat/long for a climate lookup")
        return self.indoor_heating_f - self.outdoor_heating_99_f

    @property
    def ground_heating_delta_t(self) -> float:
        """Heating ΔT for below-grade surfaces — coupled to soil, not outdoor air.

        Clamped at 0: if the soil is warmer than the indoor setpoint the surface
        gains heat rather than losing it, which a heating load shouldn't count.
        """
        return max(0.0, self.indoor_heating_f - self.ground_temp_f)


@dataclass(frozen=True)
class Cooling:
    indoor_f: float
    outdoor_1_f: float | None   # 1% cooling design temp; None -> resolved from lat/long
    shgc: float                 # window solar heat gain coefficient (0..1)
    occupants: float            # for internal + latent gains
    # Design-day attic air temperature. None -> loads.py estimates it from the outdoor
    # design temp (spaces.sol_air_attic_temp_f). Outranked by an observed
    # `spaces.attic.summer_temp_f`; overrides the estimate everywhere else.
    attic_temp_f: float | None = None

    @property
    def cooling_delta_t(self) -> float:
        if self.outdoor_1_f is None:
            raise ValueError("cooling.outdoor_1_f is unresolved — set it in the side-car "
                             "or provide the model's lat/long for a climate lookup")
        return self.outdoor_1_f - self.indoor_f


@dataclass(frozen=True)
class DuctRunSpec:
    name: str
    cfm: float


@dataclass(frozen=True)
class Ducts:
    friction_rate: float                        # in.wc/100ft; fallback when not model-derived
    runs: tuple[DuctRunSpec, ...] = ()          # hand-listed runs; empty -> derive from the model
    unit_name: str = "air handler"              # furniture-name substring locating the air handler
    available_static_pressure: float | None = None  # in.wc; enables friction-rate derivation
    fitting_factor: float = 1.5                 # straight length -> total effective length multiplier


@dataclass(frozen=True)
class SideCar:
    assemblies: dict[str, float]
    design: DesignConditions
    infiltration_ach: float
    existing_tons: float | None = None   # current equipment nominal tonnage (Manual S check)
    cooling: Cooling | None = None       # optional cooling design conditions (Manual J 1b)
    ducts: Ducts | None = None           # optional duct runs for Manual D sizing
    # explicit per-wall boundary overrides: SH3D wall id -> one of WALL_BOUNDARIES.
    # Untagged walls fall back to geometric inference.
    wall_boundaries: dict[str, str] = field(default_factory=dict)
    # buffer-space temperature policies, keyed by space name (attic / crawlspace / ...)
    spaces: dict[str, spaces_mod.SpacePolicy] = field(default_factory=dict)
    # per-level overrides keyed by the level's SH3D *name* (role / height / void naming)
    levels: dict[str, LevelSpec] = field(default_factory=dict)


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


def _optional_number(d: dict, key: str, ctx: str) -> float | None:
    """Like _require_number but returns None when the key is absent or null."""
    if d.get(key) is None:
        return None
    return _require_number(d, key, ctx)


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
            outdoor_1_f=_optional_number(cooling_raw, "outdoor_1_f", "cooling"),
            shgc=_require_number(cooling_raw, "shgc", "cooling"),
            occupants=_require_number(cooling_raw, "occupants", "cooling"),
            attic_temp_f=_optional_number(cooling_raw, "attic_temp_f", "cooling"),
        )
    ducts_raw = raw.get("ducts")
    if ducts_raw is not None and not isinstance(ducts_raw, dict):
        raise ValueError("ducts must be a mapping")
    ducts = None
    if ducts_raw is not None:
        runs = ()
        runs_raw = ducts_raw.get("runs")   # optional now — absent means "derive from the model"
        if runs_raw is not None:
            if not isinstance(runs_raw, list) or not runs_raw:
                raise ValueError("ducts.runs must be a non-empty list")
            runs_list = []
            for i, r in enumerate(runs_raw):
                if not isinstance(r, dict):
                    raise ValueError(f"ducts.runs[{i}] must be a mapping with name + cfm")
                runs_list.append(
                    DuctRunSpec(name=str(_require(r, "name", "ducts.run")),
                                cfm=_require_number(r, "cfm", f"ducts.run[{r.get('name', i)}]")))
            runs = tuple(runs_list)
        fr = ducts_raw.get("friction_rate")
        unit_name = ducts_raw.get("unit_name")
        ff = ducts_raw.get("fitting_factor")
        ducts = Ducts(
            friction_rate=(ductd.DEFAULT_FRICTION_RATE if fr is None
                           else _require_number(ducts_raw, "friction_rate", "ducts")),
            runs=runs,
            unit_name=str(unit_name) if unit_name is not None else "air handler",
            available_static_pressure=_optional_number(
                ducts_raw, "available_static_pressure", "ducts"),
            fitting_factor=1.5 if ff is None else _require_number(ducts_raw, "fitting_factor", "ducts"),
        )
    walls_raw = raw.get("walls")
    if walls_raw is not None and not isinstance(walls_raw, dict):
        raise ValueError("walls must be a mapping of wall-id -> {boundary: ...}")
    wall_boundaries: dict[str, str] = {}
    if walls_raw is not None:
        for wid, spec in walls_raw.items():
            if not isinstance(spec, dict):
                raise ValueError(f"walls['{wid}'] must be a mapping with a 'boundary' key")
            boundary = _require(spec, "boundary", f"walls['{wid}']")
            # isinstance guard first: an unhashable list/mapping would make the set
            # membership raise TypeError instead of our clean schema error.
            if not isinstance(boundary, str) or boundary not in WALL_BOUNDARIES:
                raise ValueError(f"walls['{wid}'].boundary must be one of "
                                 f"{sorted(WALL_BOUNDARIES)} (got {boundary!r})")
            wall_boundaries[str(wid)] = boundary
    spaces_raw = raw.get("spaces")
    if spaces_raw is not None and not isinstance(spaces_raw, dict):
        raise ValueError("spaces must be a mapping of space-name -> policy")
    space_policies: dict[str, spaces_mod.SpacePolicy] = {}
    for name, spec in (spaces_raw or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(f"spaces['{name}'] must be a mapping "
                             f"(winter_temp_f / summer_temp_f / factor / vented)")
        vented = spec.get("vented")
        if vented is not None and not isinstance(vented, bool):
            raise ValueError(f"spaces['{name}'].vented must be true or false")
        space_policies[str(name)] = spaces_mod.SpacePolicy(
            name=str(name),
            winter_temp_f=_optional_number(spec, "winter_temp_f", f"spaces['{name}']"),
            summer_temp_f=_optional_number(spec, "summer_temp_f", f"spaces['{name}']"),
            factor=_optional_number(spec, "factor", f"spaces['{name}']"),
            vented=vented,
        )
    levels_raw = raw.get("levels")
    if levels_raw is not None and not isinstance(levels_raw, dict):
        raise ValueError("levels must be a mapping of level-name -> spec")
    level_specs: dict[str, LevelSpec] = {}
    for name, spec in (levels_raw or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(f"levels['{name}'] must be a mapping "
                             f"(role / height_ft / below_void / above_void)")
        role = spec.get("role")
        if role is not None and (not isinstance(role, str) or role not in LEVEL_ROLES):
            raise ValueError(f"levels['{name}'].role must be one of {sorted(LEVEL_ROLES)} "
                             f"(got {role!r})")
        height_ft = _optional_number(spec, "height_ft", f"levels['{name}']")
        if height_ft is not None and (not math.isfinite(height_ft) or height_ft <= 0):
            raise ValueError(f"levels['{name}'].height_ft must be a finite number > 0")
        level_specs[str(name)] = LevelSpec(
            role=role, height_ft=height_ft,
            below_void=None if spec.get("below_void") is None else str(spec["below_void"]),
            above_void=None if spec.get("above_void") is None else str(spec["above_void"]),
        )
    sc = SideCar(
        assemblies={k: float(v) for k, v in _require(raw, "assemblies", "root").items()},
        design=DesignConditions(
            indoor_heating_f=float(_require(design, "indoor_heating_f", "design")),
            outdoor_heating_99_f=_optional_number(design, "outdoor_heating_99_f", "design"),
            supply_air_rise_f=float(_require(design, "supply_air_rise_f", "design")),
            ground_temp_f=(DEFAULT_GROUND_TEMP_F if design.get("ground_temp_f") is None
                           else _require_number(design, "ground_temp_f", "design")),
        ),
        infiltration_ach=float(_require(infil, "ach", "infiltration")),
        existing_tons=None if existing_tons is None else float(existing_tons),
        cooling=cooling,
        ducts=ducts,
        wall_boundaries=wall_boundaries,
        spaces=space_policies,
        levels=level_specs,
    )
    _validate(sc)
    return sc


def _validate(sc: SideCar) -> None:
    numeric = {
        "design.indoor_heating_f": sc.design.indoor_heating_f,
        "design.supply_air_rise_f": sc.design.supply_air_rise_f,
        "design.ground_temp_f": sc.design.ground_temp_f,
        "infiltration.ach": sc.infiltration_ach,
    }
    if sc.design.outdoor_heating_99_f is not None:   # optional — may be looked up
        numeric["design.outdoor_heating_99_f"] = sc.design.outdoor_heating_99_f
    numeric.update({f"assemblies.{k}": v for k, v in sc.assemblies.items()})
    for name, val in numeric.items():
        if not math.isfinite(val):
            raise ValueError(f"{name} must be a finite number (got {val!r})")
    if sc.design.supply_air_rise_f <= 0:
        raise ValueError("design.supply_air_rise_f must be > 0 (it sizes CFM)")
    if sc.design.outdoor_heating_99_f is not None and sc.design.heating_delta_t <= 0:
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
        cnum = {"cooling.indoor_f": c.indoor_f, "cooling.shgc": c.shgc,
                "cooling.occupants": c.occupants}
        if c.outdoor_1_f is not None:   # optional — may be looked up
            cnum["cooling.outdoor_1_f"] = c.outdoor_1_f
        for name, val in cnum.items():
            if not math.isfinite(val):
                raise ValueError(f"{name} must be a finite number (got {val!r})")
        if c.outdoor_1_f is not None and c.cooling_delta_t <= 0:
            raise ValueError("cooling: outdoor_1_f must exceed indoor_f")
        if not 0.0 <= c.shgc <= 1.0:
            raise ValueError("cooling.shgc must be between 0 and 1")
        if c.occupants < 0:
            raise ValueError("cooling.occupants must be >= 0")
        if c.attic_temp_f is not None:
            # Checked separately from `cnum` so the message can say what the bound means:
            # the whole point of the override is an attic HOTTER than the house, and an
            # attic at or below the setpoint is a typo (or Celsius), not a design input.
            if not math.isfinite(c.attic_temp_f):
                raise ValueError(
                    f"cooling.attic_temp_f must be a finite number (got {c.attic_temp_f!r})")
            if c.attic_temp_f <= c.indoor_f:
                raise ValueError(
                    f"cooling.attic_temp_f ({c.attic_temp_f}) must exceed cooling.indoor_f "
                    f"({c.indoor_f}) — it is a hot-attic design temperature in °F")
    if sc.ducts is not None:
        if not math.isfinite(sc.ducts.friction_rate) or sc.ducts.friction_rate <= 0:
            raise ValueError("ducts.friction_rate must be finite and > 0")
        if not math.isfinite(sc.ducts.fitting_factor) or sc.ducts.fitting_factor <= 0:
            raise ValueError("ducts.fitting_factor must be finite and > 0")
        asp = sc.ducts.available_static_pressure
        if asp is not None and (not math.isfinite(asp) or asp <= 0):
            raise ValueError("ducts.available_static_pressure must be finite and > 0")
        for run in sc.ducts.runs:
            if not math.isfinite(run.cfm) or run.cfm <= 0:
                raise ValueError(f"ducts.run '{run.name}': cfm must be finite and > 0")
    for name, p in sc.spaces.items():
        for label, val in (("winter_temp_f", p.winter_temp_f), ("summer_temp_f", p.summer_temp_f),
                           ("factor", p.factor)):
            if val is not None and not math.isfinite(val):
                raise ValueError(f"spaces['{name}'].{label} must be a finite number")
        if p.factor is not None and p.factor < 0:
            raise ValueError(f"spaces['{name}'].factor must be >= 0")
