from eldr import diff


def _run(total=40000.0, cfm=1245.0, walls=10000.0, windows=5000.0, infil=11000.0,
         station="Newark, NJ", heat_dt=56.0, rise=30.0, ach=0.68,
         tons=3.4, rec=3.5, verdict="oversized", rooms=None):
    return {
        "design": {"heating_delta_t_f": heat_dt, "supply_air_rise_f": rise},
        "station": {"name": station},
        "infiltration_ach": ach,
        "heating": {
            "total_btuh": total, "infiltration_btuh": infil, "cfm": cfm,
            "by_category": {"exterior_wall": walls, "window": windows},
        },
        "cooling": None,
        "equipment_sizing": {
            "design_load_tons": tons, "recommended_tons": rec, "verdict": verdict,
        },
        "rooms": rooms if rooms is not None else [
            {"name": "Kitchen", "design_cfm": 254.0},
            {"name": "Main Bed", "design_cfm": 225.0},
        ],
    }


def test_identical_runs_report_no_change():
    md = diff.render_diff(_run(), _run())
    assert "**No change.**" in md
    assert "| Component |" not in md


def test_a_moved_component_is_reported_with_delta_and_percent():
    md = diff.render_diff(_run(), _run(walls=11000.0, total=41000.0))
    assert "`exterior_wall`" in md
    assert "+1,000" in md
    assert "+10.0%" in md


def test_a_component_below_the_threshold_is_not_reported():
    """Otherwise every run prints a wall of rows that moved by rounding."""
    md = diff.render_diff(_run(), _run(walls=10010.0, total=40010.0))
    assert "`exterior_wall`" not in md
    assert "No component moved past the reporting threshold" in md


def test_a_design_condition_change_is_called_out_before_the_components():
    """Everything moves together when the station changes. Without this banner a
    reader treats a dozen correlated rows as a dozen separate findings."""
    md = diff.render_diff(
        _run(station="New York, NY", heat_dt=55.0, total=39694.0),
        _run(station="Newark, NJ", heat_dt=56.0, total=40331.0))
    assert "Design conditions changed" in md
    assert "New York, NY → Newark, NJ" in md
    assert md.index("Design conditions changed") < md.index("### Heating")


def test_no_condition_banner_when_only_geometry_moved():
    md = diff.render_diff(_run(), _run(walls=12000.0, total=42000.0))
    assert "Design conditions changed" not in md


def test_sizing_changes_are_reported_even_when_small():
    """A tenth of a ton is below every other threshold here and still decides
    which unit gets quoted."""
    md = diff.render_diff(_run(tons=3.4, rec=3.5), _run(tons=3.6, rec=4.0))
    assert "3.4 → 3.6 tons" in md
    assert "Recommended size 3.5 → 4.0 tons" in md


def test_a_flipped_verdict_is_reported():
    md = diff.render_diff(_run(verdict="oversized"), _run(verdict="well-matched"))
    assert "oversized → well-matched" in md


def test_added_and_removed_rooms_are_named():
    md = diff.render_diff(
        _run(rooms=[{"name": "Kitchen", "design_cfm": 254.0}]),
        _run(rooms=[{"name": "Kitchen", "design_cfm": 254.0},
                    {"name": "Mud Room", "design_cfm": 40.0}]))
    assert "**Added**: Mud Room" in md


def test_room_airflow_changes_are_ranked_by_size():
    md = diff.render_diff(
        _run(rooms=[{"name": "Kitchen", "design_cfm": 254.0},
                    {"name": "Main Bed", "design_cfm": 225.0}]),
        _run(rooms=[{"name": "Kitchen", "design_cfm": 259.0},
                    {"name": "Main Bed", "design_cfm": 275.0}]))
    assert md.index("Main Bed") < md.index("| Kitchen")   # +50 before +5


def test_cooling_section_appears_only_when_cooling_is_present():
    with_cool = _run()
    with_cool["cooling"] = {"total_btuh": 23125.0, "cfm": 844.0,
                            "infiltration_btuh": 3302.0,
                            "by_category": {"ceiling": 3284.0}}
    after = _run()
    after["cooling"] = dict(with_cool["cooling"], total_btuh=24000.0,
                            by_category={"ceiling": 4000.0})
    assert "### Cooling" in diff.render_diff(with_cool, after)
    assert "### Cooling" not in diff.render_diff(_run(), _run(walls=12000.0))
