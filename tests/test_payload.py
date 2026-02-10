"""Tests for the payload gate."""

from __future__ import annotations

from axiom_tfg.gates.payload import check_payload
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
    mass_kg: float,
    max_payload_kg: float,
    *,
    can_split_payload: bool = False,
    can_change_constructor: bool = False,
) -> TaskSpec:
    return TaskSpec(
        task_id="test-payload",
        meta=MetaSpec(template="pick_and_place"),
        substrate=SubstrateSpec(id="obj", mass_kg=mass_kg, initial_pose=XYZ(xyz=[0, 0, 0])),
        transformation=TransformationSpec(
            target_pose=XYZ(xyz=[1, 0, 0]),
            tolerance_m=0.01,
        ),
        constructor=ConstructorSpec(
            id="arm",
            base_pose=XYZ(xyz=[0, 0, 0]),
            max_reach_m=2.0,
            max_payload_kg=max_payload_kg,
        ),
        allowed_adjustments=AllowedAdjustments(
            can_split_payload=can_split_payload,
            can_change_constructor=can_change_constructor,
        ),
    )


def test_within_payload_passes() -> None:
    spec = _make_spec(mass_kg=2.0, max_payload_kg=5.0)
    result, fixes = check_payload(spec)
    assert result.status == GateStatus.PASS
    assert result.reason_code is None
    assert fixes == []


def test_exactly_at_limit_passes() -> None:
    spec = _make_spec(mass_kg=5.0, max_payload_kg=5.0)
    result, fixes = check_payload(spec)
    assert result.status == GateStatus.PASS


def test_over_payload_fails() -> None:
    spec = _make_spec(mass_kg=10.0, max_payload_kg=5.0)
    result, fixes = check_payload(spec)
    assert result.status == GateStatus.FAIL
    assert result.reason_code == "OVER_PAYLOAD"
    assert fixes == []


def test_over_payload_split_fix() -> None:
    spec = _make_spec(mass_kg=12.0, max_payload_kg=5.0, can_split_payload=True)
    result, fixes = check_payload(spec)
    assert result.status == GateStatus.FAIL
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix.type == FixType.SPLIT_PAYLOAD
    assert fix.proposed_patch["suggested_payload_split_count"] == 3  # ceil(12/5)


def test_over_payload_change_constructor_fix() -> None:
    spec = _make_spec(mass_kg=10.0, max_payload_kg=5.0, can_change_constructor=True)
    result, fixes = check_payload(spec)
    assert result.status == GateStatus.FAIL
    assert len(fixes) == 1
    assert fixes[0].type == FixType.CHANGE_CONSTRUCTOR


def test_over_payload_both_fixes() -> None:
    spec = _make_spec(
        mass_kg=25.0,
        max_payload_kg=5.0,
        can_split_payload=True,
        can_change_constructor=True,
    )
    result, fixes = check_payload(spec)
    assert result.status == GateStatus.FAIL
    assert len(fixes) == 2
    assert fixes[0].type == FixType.SPLIT_PAYLOAD
    assert fixes[0].proposed_patch["suggested_payload_split_count"] == 5
    assert fixes[1].type == FixType.CHANGE_CONSTRUCTOR
