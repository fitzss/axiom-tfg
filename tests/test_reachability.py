"""Tests for the reachability gate."""

from __future__ import annotations

import math

from axiom_tfg.gates.reachability import check_reachability
from axiom_tfg.models import (
    AllowedAdjustments,
    ConstructorSpec,
    FixType,
    GateStatus,
    MetaSpec,
    SubstrateSpec,
    TaskSpec,
    TransformationSpec,
    XYZ,
)


def _make_spec(
    base: list[float],
    target: list[float],
    max_reach: float,
    *,
    can_move_target: bool = False,
    can_move_base: bool = False,
    can_change_constructor: bool = False,
) -> TaskSpec:
    return TaskSpec(
        task_id="test-reach",
        meta=MetaSpec(template="pick_and_place"),
        substrate=SubstrateSpec(id="obj", mass_kg=1.0, initial_pose=XYZ(xyz=[0, 0, 0])),
        transformation=TransformationSpec(
            target_pose=XYZ(xyz=target),
            tolerance_m=0.01,
        ),
        constructor=ConstructorSpec(
            id="arm",
            base_pose=XYZ(xyz=base),
            max_reach_m=max_reach,
            max_payload_kg=10.0,
        ),
        allowed_adjustments=AllowedAdjustments(
            can_move_target=can_move_target,
            can_move_base=can_move_base,
            can_change_constructor=can_change_constructor,
        ),
    )


def test_reachable_passes() -> None:
    spec = _make_spec(base=[0, 0, 0], target=[1, 0, 0], max_reach=2.0)
    result, fixes = check_reachability(spec)
    assert result.status == GateStatus.PASS
    assert result.reason_code is None
    assert fixes == []


def test_exactly_at_boundary_passes() -> None:
    spec = _make_spec(base=[0, 0, 0], target=[2, 0, 0], max_reach=2.0)
    result, fixes = check_reachability(spec)
    assert result.status == GateStatus.PASS
    assert fixes == []


def test_unreachable_fails() -> None:
    spec = _make_spec(base=[0, 0, 0], target=[3, 4, 0], max_reach=1.0)
    result, fixes = check_reachability(spec)
    assert result.status == GateStatus.FAIL
    assert result.reason_code == "OUT_OF_REACH"
    assert result.measured_values["distance_m"] == 5.0
    assert fixes == []  # no adjustments allowed


def test_unreachable_move_target_fix() -> None:
    spec = _make_spec(
        base=[0, 0, 0],
        target=[3, 4, 0],
        max_reach=1.0,
        can_move_target=True,
    )
    result, fixes = check_reachability(spec)
    assert result.status == GateStatus.FAIL
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix.type == FixType.MOVE_TARGET
    assert fix.delta == 4.0  # 5.0 - 1.0

    projected = fix.proposed_patch["projected_target_xyz"]
    dist_from_base = math.sqrt(sum(v**2 for v in projected))
    assert abs(dist_from_base - 1.0) < 1e-6


def test_unreachable_move_base_fix() -> None:
    spec = _make_spec(
        base=[0, 0, 0],
        target=[5, 0, 0],
        max_reach=2.0,
        can_move_base=True,
    )
    result, fixes = check_reachability(spec)
    assert result.status == GateStatus.FAIL
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix.type == FixType.MOVE_BASE
    assert fix.delta == 3.0
    new_base = fix.proposed_patch["suggested_base_xyz"]
    assert abs(new_base[0] - 3.0) < 1e-6


def test_unreachable_multiple_fixes() -> None:
    """When multiple adjustments are allowed, all are returned in order."""
    spec = _make_spec(
        base=[0, 0, 0],
        target=[3, 0, 0],
        max_reach=1.0,
        can_move_target=True,
        can_move_base=True,
        can_change_constructor=True,
    )
    _, fixes = check_reachability(spec)
    assert len(fixes) == 3
    assert [f.type for f in fixes] == [
        FixType.MOVE_TARGET,
        FixType.MOVE_BASE,
        FixType.CHANGE_CONSTRUCTOR,
    ]
