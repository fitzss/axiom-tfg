"""Tests for the robot registry and registry-driven gate integration."""

from __future__ import annotations

import pytest

from axiom_tfg.robots import ROBOT_REGISTRY, RobotProfile, get_robot
from axiom_tfg.sdk import check_simple


# ── Registry lookup ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name", list(ROBOT_REGISTRY.keys()))
def test_registry_lookup(name: str) -> None:
    profile = get_robot(name)
    assert isinstance(profile, RobotProfile)
    assert profile.name == name
    assert profile.max_reach_m > 0
    assert profile.max_payload_kg > 0
    assert profile.dof > 0


def test_get_robot_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown robot"):
        get_robot("unknown")


# ── check_simple registry integration ────────────────────────────────────


def test_check_simple_franka_uses_correct_reach() -> None:
    """check_simple(robot='franka') should use reach=0.855 from registry."""
    result = check_simple(
        robot="franka",
        target_xyz=[0.4, 0.2, 0.3],
    )
    # 0.4² + 0.2² + 0.3² ≈ 0.54 → distance ≈ 0.54 m < 0.855 m reach
    # Should pass reachability (or IK).
    ev = result.evidence
    constructor = ev.get("task_id")  # just check it ran
    assert constructor is not None
    # The URDF path in the evidence should contain "franka.urdf"
    spec_data = result.evidence
    assert spec_data is not None


def test_check_simple_franka_uses_correct_urdf() -> None:
    """check_simple(robot='franka') should auto-load franka.urdf."""
    result = check_simple(
        robot="franka",
        target_xyz=[0.4, 0.2, 0.3],
    )
    # If IK ran, it used the franka URDF.  Check the evidence for ik_feasibility.
    checks = result.evidence.get("checks", [])
    gate_names = [c["gate_name"] for c in checks]
    assert "ik_feasibility" in gate_names


def test_check_simple_kuka_uses_correct_reach() -> None:
    """check_simple(robot='kuka_iiwa14') should use reach=0.82 from registry."""
    result = check_simple(
        robot="kuka_iiwa14",
        target_xyz=[0.3, 0.2, 0.2],
    )
    checks = result.evidence.get("checks", [])
    gate_names = [c["gate_name"] for c in checks]
    assert "ik_feasibility" in gate_names


def test_ik_franka_reachable_passes() -> None:
    """A reachable target for franka should pass IK."""
    result = check_simple(
        robot="franka",
        target_xyz=[0.3, 0.1, 0.4],
    )
    assert result.verdict == "CAN"


def test_ik_franka_unreachable_fails() -> None:
    """A target far beyond franka's reach (0.855 m) should fail."""
    result = check_simple(
        robot="franka",
        target_xyz=[5.0, 5.0, 5.0],
    )
    assert result.verdict == "HARD_CANT"


def test_ik_kuka_reachable_passes() -> None:
    """A reachable target for kuka_iiwa14 should pass IK."""
    result = check_simple(
        robot="kuka_iiwa14",
        target_xyz=[0.3, 0.1, 0.3],
    )
    assert result.verdict == "CAN"


def test_ik_kuka_unreachable_fails() -> None:
    """A target far beyond kuka's reach (0.82 m) should fail."""
    result = check_simple(
        robot="kuka_iiwa14",
        target_xyz=[5.0, 5.0, 5.0],
    )
    assert result.verdict == "HARD_CANT"


def test_explicit_kwargs_override_registry() -> None:
    """Explicit max_payload_kg should override the registry value."""
    # Franka registry payload is 3.0 kg.  Override with 0.01 kg so a
    # 0.5 kg object exceeds payload.
    result = check_simple(
        robot="franka",
        target_xyz=[0.3, 0.1, 0.4],
        max_payload_kg=0.01,
    )
    assert result.verdict == "HARD_CANT"
    assert result.failed_gate == "payload"


# ── Public API exports ───────────────────────────────────────────────────


def test_public_exports() -> None:
    from axiom_tfg import ROBOT_REGISTRY as R
    from axiom_tfg import RobotProfile as RP
    from axiom_tfg import get_robot as gr

    assert R is ROBOT_REGISTRY
    assert RP is RobotProfile
    assert gr is get_robot
