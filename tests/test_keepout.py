"""Tests for the keepout zone gate."""

from __future__ import annotations

import math

from axiom_tfg.gates.keepout import check_keepout
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
    target: list[float],
    zones: list[KeepoutZone],
    *,
    safety_buffer: float = 0.02,
    can_move_target: bool = False,
) -> TaskSpec:
    return TaskSpec(
        task_id="test-keepout",
        meta=MetaSpec(template="pick_and_place"),
        substrate=SubstrateSpec(id="obj", mass_kg=1.0, initial_pose=XYZ(xyz=[0, 0, 0])),
        transformation=TransformationSpec(
            target_pose=XYZ(xyz=target),
            tolerance_m=0.01,
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


ZONE = KeepoutZone(id="box", min_xyz=[1.0, 1.0, 1.0], max_xyz=[3.0, 3.0, 3.0])


# ── basic pass / fail ─────────────────────────────────────────────────────


def test_outside_zone_passes() -> None:
    spec = _make_spec(target=[0.0, 0.0, 0.0], zones=[ZONE])
    result, fixes = check_keepout(spec)
    assert result.status == GateStatus.PASS
    assert fixes == []


def test_no_zones_passes() -> None:
    spec = _make_spec(target=[2.0, 2.0, 2.0], zones=[])
    result, fixes = check_keepout(spec)
    assert result.status == GateStatus.PASS


def test_inside_zone_fails() -> None:
    spec = _make_spec(target=[2.0, 2.0, 2.0], zones=[ZONE])
    result, fixes = check_keepout(spec)
    assert result.status == GateStatus.FAIL
    assert result.reason_code == "IN_KEEP_OUT_ZONE"
    assert result.measured_values["violating_zone_id"] == "box"


# ── safety buffer behaviour ───────────────────────────────────────────────


def test_inside_buffer_margin_fails() -> None:
    """Point is outside the raw AABB but inside the expanded (buffered) AABB."""
    # ZONE max_xyz[0] = 3.0, buffer = 0.02 → expanded max = 3.02
    spec = _make_spec(target=[3.01, 2.0, 2.0], zones=[ZONE], safety_buffer=0.02)
    result, _ = check_keepout(spec)
    assert result.status == GateStatus.FAIL


def test_outside_buffer_passes() -> None:
    """Point is just outside the expanded AABB → should pass."""
    spec = _make_spec(target=[3.03, 2.0, 2.0], zones=[ZONE], safety_buffer=0.02)
    result, _ = check_keepout(spec)
    assert result.status == GateStatus.PASS


def test_zero_buffer_on_boundary_passes() -> None:
    """With zero buffer, a point exactly on the AABB boundary is considered inside
    (the boundary is inclusive), but a point at max + epsilon should pass."""
    spec = _make_spec(target=[3.0, 2.0, 2.0], zones=[ZONE], safety_buffer=0.0)
    result, _ = check_keepout(spec)
    # On the boundary → still inside the closed AABB
    assert result.status == GateStatus.FAIL


# ── counterfactual fix ────────────────────────────────────────────────────


def test_fix_moves_outside_by_at_least_buffer() -> None:
    spec = _make_spec(
        target=[2.0, 2.0, 2.0],
        zones=[ZONE],
        safety_buffer=0.05,
        can_move_target=True,
    )
    result, fixes = check_keepout(spec)
    assert result.status == GateStatus.FAIL
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix.type == FixType.MOVE_TARGET

    escaped = fix.proposed_patch["projected_target_xyz"]
    # The escaped point must lie outside the expanded AABB.
    expanded_min = [v - 0.05 for v in ZONE.min_xyz]
    expanded_max = [v + 0.05 for v in ZONE.max_xyz]
    outside = False
    for i in range(3):
        if escaped[i] <= expanded_min[i] or escaped[i] >= expanded_max[i]:
            outside = True
    assert outside, f"Escaped point {escaped} is still inside expanded AABB"

    # Delta must equal the L2 distance of the move.
    dist = math.sqrt(sum((a - b) ** 2 for a, b in zip([2.0, 2.0, 2.0], escaped)))
    assert abs(fix.delta - dist) < 1e-6


def test_fix_chooses_nearest_face() -> None:
    """Target at (2.9, 2.0, 2.0) with buffer=0.05 → nearest face is +x at 3.05.
    Escape distance = 3.05 - 2.9 = 0.15."""
    spec = _make_spec(
        target=[2.9, 2.0, 2.0],
        zones=[ZONE],
        safety_buffer=0.05,
        can_move_target=True,
    )
    _, fixes = check_keepout(spec)
    fix = fixes[0]
    assert abs(fix.delta - 0.15) < 1e-6
    assert abs(fix.proposed_patch["projected_target_xyz"][0] - 3.05) < 1e-6


def test_no_fix_when_adjustment_disallowed() -> None:
    spec = _make_spec(
        target=[2.0, 2.0, 2.0],
        zones=[ZONE],
        can_move_target=False,
    )
    result, fixes = check_keepout(spec)
    assert result.status == GateStatus.FAIL
    assert fixes == []


# ── pipeline short-circuit behaviour ──────────────────────────────────────


def test_reachability_fail_skips_keepout() -> None:
    """When reachability fails, keepout should never be evaluated."""
    from axiom_tfg.evidence import run_gates

    spec = TaskSpec(
        task_id="test-sc-reach",
        meta=MetaSpec(template="pick_and_place"),
        substrate=SubstrateSpec(id="obj", mass_kg=1.0, initial_pose=XYZ(xyz=[0, 0, 0])),
        transformation=TransformationSpec(
            target_pose=XYZ(xyz=[100.0, 0.0, 0.0]),  # way out of reach
            tolerance_m=0.01,
        ),
        constructor=ConstructorSpec(
            id="arm",
            base_pose=XYZ(xyz=[0, 0, 0]),
            max_reach_m=1.0,
            max_payload_kg=10.0,
        ),
        environment=EnvironmentSpec(
            keepout_zones=[ZONE],
        ),
    )
    packet = run_gates(spec)
    gate_names = [c.gate_name for c in packet.checks]
    assert "reachability" in gate_names
    assert "keepout" not in gate_names
    assert packet.failed_gate == "reachability"


def test_payload_fail_skips_keepout() -> None:
    """When payload fails, keepout should never be evaluated."""
    from axiom_tfg.evidence import run_gates

    spec = TaskSpec(
        task_id="test-sc-payload",
        meta=MetaSpec(template="pick_and_place"),
        substrate=SubstrateSpec(id="obj", mass_kg=100.0, initial_pose=XYZ(xyz=[0, 0, 0])),
        transformation=TransformationSpec(
            target_pose=XYZ(xyz=[1.0, 0.0, 0.0]),
            tolerance_m=0.01,
        ),
        constructor=ConstructorSpec(
            id="arm",
            base_pose=XYZ(xyz=[0, 0, 0]),
            max_reach_m=10.0,
            max_payload_kg=1.0,
        ),
        environment=EnvironmentSpec(
            keepout_zones=[ZONE],
        ),
    )
    packet = run_gates(spec)
    gate_names = [c.gate_name for c in packet.checks]
    assert "payload" in gate_names
    assert "keepout" not in gate_names
    assert packet.failed_gate == "payload"
