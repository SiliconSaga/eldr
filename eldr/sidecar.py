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

# Every surface category the engine can produce. A variant assembly key must name one of
# these before its slash — that check is the whole validation story for variants, and it
# is why a typo like `windwo/single` fails at load instead of silently never matching.
CATEGORIES = frozenset({
    "exterior_wall", "basement_wall", "window", "door",
    "ceiling", "floor", "buffer_wall", "buffer_floor", "exposed_floor",
})


def split_assembly_key(key: str) -> tuple[str, str | None]:
    """`(category, variant)` for an assembly key; variant is None for a bare category.

    Splits on the FIRST slash only, so a variant name may itself contain slashes.

    Use this for EVERY assembly-key comparison. Never substring-match one:
    `exterior_wall/r0` contains `exterior_wall`, so an `in` test or a `startswith`
    silently conflates a variant with its category default — structurally the same trap
    as `"floor"` matching inside `"buffer_floor"`, which cost the level-stack work
    several rounds. Compare the returned parts exactly.
    """
    category, sep, variant = key.partition("/")
    return (category, variant if sep else None)


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
    # Deep-soil temp. COOLING ONLY: it decides whether the soil is a heat source in summer
    # (almost never — see loads.GROUND_COUPLED_CATEGORIES). Heating does NOT read it, and
    # there is deliberately no `ground_heating_delta_t` counterpart: a below-grade
    # assembly U is already an effective value containing the soil path, so discounting
    # the winter ΔT for soil as well would count the same resistance twice.
    ground_temp_f: float = DEFAULT_GROUND_TEMP_F

    @property
    def heating_delta_t(self) -> float:
        if self.outdoor_heating_99_f is None:
            raise ValueError("outdoor_heating_99_f is unresolved — set it in the side-car "
                             "or provide the model's lat/long for a climate lookup")
        return self.indoor_heating_f - self.outdoor_heating_99_f


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

        def _void(key, _spec=spec, _name=name):
            """A void name must BE a name. `str()` on whatever YAML produced accepted a
            list or a mapping as one, and the stringified result matches no `spaces:` entry
            and no built-in policy, so it fell through to the bare 0.5 buffer factor — a
            load computed from a typo, with nothing said."""
            v = _spec.get(key)
            if v is None:
                return None
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"levels['{_name}'].{key} must be a non-empty space name "
                                 f"(got {v!r})")
            return v

        level_specs[str(name)] = LevelSpec(
            role=role, height_ft=height_ft,
            below_void=_void("below_void"), above_void=_void("above_void"),
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
    # Variant keys are validated against the category list; bare keys are not. An
    # unrecognised BARE key has always been accepted and ignored (side-cars carry
    # note-to-self entries), and tightening that is a separate decision. A variant is
    # different: it exists only to be pointed at by a tag, and a variant whose category
    # is misspelled can never match any surface, so it would fail silently forever.
    for name in sc.assemblies:
        category, variant = split_assembly_key(name)
        if variant is not None and category not in CATEGORIES:
            raise ValueError(
                f"assemblies.{name}: unknown category '{category}' — a variant key is "
                f"'<category>/<name>' and the category must be one of "
                f"{sorted(CATEGORIES)}")
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
        # The attic's summer temperature is the SAME quantity as `cooling.attic_temp_f`
        # arriving by a stronger route (an observation outranks the side-car's design
        # figure), so it takes the same bound — which the other route has had all along
        # and this one did not. Below the setpoint there is no negative-ΔT branch to fall
        # into: `spaces._factor` clamps at 0, so the ceiling silently takes ZERO cooling
        # load. Nothing raises and no number looks wrong; the gain is simply absent.
        # ATTIC ONLY, deliberately. A crawlspace or garage at or below the setpoint is an
        # ordinary observation — a cool crawl genuinely contributes no cooling load, and
        # the 0 clamp is the right answer there. The attic is the exception because the
        # whole reason to declare its summer temperature is that it runs HOTTER than the
        # house; at or below the setpoint it is a typo, or Celsius, not a design input.
        if (name == spaces_mod.ATTIC_SPACE and sc.cooling is not None
                and p.summer_temp_f is not None and p.summer_temp_f <= sc.cooling.indoor_f):
            raise ValueError(
                f"spaces['{name}'].summer_temp_f ({p.summer_temp_f}) must exceed "
                f"cooling.indoor_f ({sc.cooling.indoor_f}) — it is a hot-attic design "
                f"temperature in °F")
        # A FRACTION of the design ΔT, so 1.0 (the space tracks outdoor air) is the cap.
        # The engine does not clamp the resolved factor above 1 — a sun-heated attic
        # genuinely runs hotter than outdoor air — but that number has to come from an
        # observed `summer_temp_f`, which says what the space IS. `factor: 2` is the
        # shorthand claiming twice the outdoor ΔT with no temperature behind it, and it
        # sails past unnoticed because a factor is a bare number with no unit to look wrong.
        if p.factor is not None and not 0.0 <= p.factor <= 1.0:
            raise ValueError(f"spaces['{name}'].factor must be between 0 and 1 — it is a "
                             f"fraction of the design ΔT (got {p.factor!r}). A space hotter "
                             f"than outdoor air is declared with summer_temp_f, not here.")
