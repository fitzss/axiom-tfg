"""Tests for the path keepout gate."""

from __future__ import annotations

from axiom_tfg.evidence import run_gates
from axiom_tfg.gates.path_keepout import check_path_keepout
from axiom_tfg.models import (
    AllowedAdjustments,
    ConstructorSpec,
    EnvironmentSpec,
    FixType,
    GateStatus,
    KeepoutZone,
    MetaSpec,
    SubstrateSpec,
    TaskSpec,
    TransformationSpec,
    XYZ,
)


def _make_spec(
    initial: list[float],
    target: list[float],
    zones: list[KeepoutZone],
    *,
    waypoints: list[list[float]] | None = None,
    safety_buffer: float = 0.02,
    can_move_target: bool = True,
) -> TaskSpec:
    wp = [XYZ(xyz=w) for w in waypoints] if waypoints else []
    return TaskSpec(
        task_id="test-path-keepout",
        meta=MetaSpec(template="pick_and_place"),
        substrate=SubstrateSpec(id="obj", mass_kg=1.0, initial_pose=XYZ(xyz=initial)),
        transformation=TransformationSpec(
            target_pose=XYZ(xyz=target),
            tolerance_m=0.01,
            waypoints=wp,
        ),
        constructor=ConstructorSpec(
            id="arm",
            base_pose=XYZ(xyz=[0, 0, 0]),
            max_reach_m=10.0,
            max_payload_kg=10.0,
        ),
        environment=EnvironmentSpec(
            keepout_zones=zones,
            safety_buffer=safety_buffer,
        ),
        allowed_adjustments=AllowedAdjustments(
            can_move_target=can_move_target,
        ),
    )


ZONE = KeepoutZone(id="safety_cage", min_xyz=[0.4, 0.4, 0.0], max_xyz=[0.6, 0.6, 1.0])


# ── basic pass / fail ─────────────────────────────────────────────────────


def test_path_through_zone_blocked() -> None:
    """Path from (0, 0, 0.5) to (1, 1, 0.5) crosses the zone."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[1.0, 1.0, 0.5],
        zones=[ZONE],
    )
    result_tuple = check_path_keepout(spec)
    assert result_tuple is not None
    result, fixes = result_tuple
    assert result.status == GateStatus.FAIL
    assert result.reason_code == "PATH_CROSSES_KEEPOUT"
    assert result.measured_values["violating_zone_id"] == "safety_cage"
    assert len(fixes) == 1
    assert fixes[0].type == FixType.MOVE_TARGET


def test_path_avoids_zone_passes() -> None:
    """Path from (0, 0, 0.5) to (0.2, 0.2, 0.5) stays clear of zone."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[0.2, 0.2, 0.5],
        zones=[ZONE],
    )
    result_tuple = check_path_keepout(spec)
    assert result_tuple is not None
    result, fixes = result_tuple
    assert result.status == GateStatus.PASS
    assert fixes == []


# ── skip conditions ──────────────────────────────────────────────────────


def test_no_initial_pose_skips() -> None:
    """When initial_pose is origin-default with no waypoints, gate is skipped."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.0],
        target=[1.0, 1.0, 0.5],
        zones=[ZONE],
    )
    assert check_path_keepout(spec) is None


def test_no_zones_passes() -> None:
    """No keepout zones → gate passes trivially."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[1.0, 1.0, 0.5],
        zones=[],
    )
    result_tuple = check_path_keepout(spec)
    assert result_tuple is not None
    result, fixes = result_tuple
    assert result.status == GateStatus.PASS


# ── waypoints ────────────────────────────────────────────────────────────


def test_waypoints_through_zone_blocked() -> None:
    """Path with waypoints that route through zone is blocked."""
    # Start and end are clear, but waypoint forces path through zone.
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[1.0, 0.0, 0.5],
        zones=[ZONE],
        waypoints=[[0.5, 0.5, 0.5]],  # right inside the zone
    )
    result_tuple = check_path_keepout(spec)
    assert result_tuple is not None
    result, fixes = result_tuple
    assert result.status == GateStatus.FAIL


def test_waypoints_around_zone_passes() -> None:
    """Path with waypoints that route around zone passes."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[1.0, 0.0, 0.5],
        zones=[ZONE],
        waypoints=[[0.0, 1.0, 0.5], [1.0, 1.0, 0.5]],  # go wide around zone
    )
    result_tuple = check_path_keepout(spec)
    assert result_tuple is not None
    result, fixes = result_tuple
    assert result.status == GateStatus.PASS


# ── fix escape direction ────────────────────────────────────────────────


def test_fix_escape_direction() -> None:
    """The escaped point should be outside the expanded zone."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[1.0, 1.0, 0.5],
        zones=[ZONE],
        safety_buffer=0.02,
    )
    result_tuple = check_path_keepout(spec)
    assert result_tuple is not None
    _, fixes = result_tuple
    assert len(fixes) == 1
    escaped = fixes[0].proposed_patch["projected_target_xyz"]
    # Escaped point must be outside the expanded AABB.
    expanded_min = [v - 0.02 for v in ZONE.min_xyz]
    expanded_max = [v + 0.02 for v in ZONE.max_xyz]
    outside = False
    for i in range(3):
        if escaped[i] <= expanded_min[i] or escaped[i] >= expanded_max[i]:
            outside = True
    assert outside, f"Escaped point {escaped} is still inside expanded AABB"


def test_no_fix_when_adjustment_disallowed() -> None:
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[1.0, 1.0, 0.5],
        zones=[ZONE],
        can_move_target=False,
    )
    result_tuple = check_path_keepout(spec)
    assert result_tuple is not None
    result, fixes = result_tuple
    assert result.status == GateStatus.FAIL
    assert fixes == []


# ── pipeline integration ────────────────────────────────────────────────


def test_pipeline_endpoint_keepout_fails_first() -> None:
    """When endpoint is in a zone, endpoint keepout fails and path gate never runs."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[0.5, 0.5, 0.5],  # target is inside the zone
        zones=[ZONE],
    )
    packet = run_gates(spec)
    gate_names = [c.gate_name for c in packet.checks]
    assert "keepout" in gate_names
    assert "path_keepout" not in gate_names
    assert packet.failed_gate == "keepout"


def test_pipeline_endpoint_passes_path_blocked() -> None:
    """Endpoint is clear but path crosses zone → path_keepout fails."""
    spec = _make_spec(
        initial=[0.0, 0.0, 0.5],
        target=[1.0, 1.0, 0.5],  # target is outside zone
        zones=[ZONE],
    )
    packet = run_gates(spec)
    gate_names = [c.gate_name for c in packet.checks]
    assert "keepout" in gate_names
    assert "path_keepout" in gate_names
    assert packet.failed_gate == "path_keepout"
    assert packet.verdict.value == "HARD_CANT"
