"""Tests for the IK feasibility gate."""

from __future__ import annotations

from pathlib import Path

from axiom_tfg.evidence import run_gates
from axiom_tfg.gates.ik_feasibility import check_ik_feasibility
from axiom_tfg.models import TaskSpec

_URDF = str(Path(__file__).resolve().parent.parent / "axiom_tfg" / "data" / "ur5e.urdf")


def _make_spec(target_xyz: list[float], *, urdf: str | None = _URDF) -> TaskSpec:
    data = {
        "task_id": "ik-test",
        "meta": {"template": "pick_and_place"},
        "substrate": {
            "id": "widget",
            "mass_kg": 0.5,
            "initial_pose": {"xyz": [0.0, 0.0, 0.0]},
        },
        "transformation": {
            "target_pose": {"xyz": target_xyz},
            "tolerance_m": 0.01,
        },
        "constructor": {
            "id": "ur5e",
            "base_pose": {"xyz": [0.0, 0.0, 0.0]},
            "max_reach_m": 1.85,
            "max_payload_kg": 5.0,
            "urdf_path": urdf,
            "base_link": "base_link",
            "ee_link": "ee_link",
        },
        "allowed_adjustments": {
            "can_move_target": True,
            "can_change_constructor": True,
        },
    }
    return TaskSpec.model_validate(data)


class TestIKGateDirect:
    """Direct calls to check_ik_feasibility."""

    def test_reachable_pose_passes(self) -> None:
        spec = _make_spec([0.4, 0.2, 0.5])
        out = check_ik_feasibility(spec)
        assert out is not None
        result, fixes = out
        assert result.status.value == "PASS"
        assert result.gate_name == "ik_feasibility"
        assert result.measured_values["ik_success"] is True
        assert result.measured_values["solver"] == "ikpy"
        assert "joint_solution" in result.measured_values
        assert fixes == []

    def test_unreachable_pose_fails(self) -> None:
        spec = _make_spec([5.0, 5.0, 5.0])
        out = check_ik_feasibility(spec)
        assert out is not None
        result, fixes = out
        assert result.status.value == "FAIL"
        assert result.reason_code == "NO_IK_SOLUTION"
        assert result.measured_values["ik_success"] is False
        assert len(fixes) >= 1
        assert fixes[0].type.value == "MOVE_TARGET"

    def test_skipped_when_no_urdf(self) -> None:
        spec = _make_spec([0.4, 0.2, 0.5], urdf=None)
        out = check_ik_feasibility(spec)
        assert out is None

    def test_missing_urdf_file_fails(self) -> None:
        spec = _make_spec([0.4, 0.2, 0.5], urdf="/nonexistent/robot.urdf")
        out = check_ik_feasibility(spec)
        assert out is not None
        result, _fixes = out
        assert result.status.value == "FAIL"
        assert result.reason_code == "URDF_NOT_FOUND"


class TestIKGateInPipeline:
    """IK gate integrated into the full gate pipeline."""

    def test_can_verdict_with_urdf(self) -> None:
        spec = _make_spec([0.4, 0.2, 0.5])
        packet = run_gates(spec)
        assert packet.verdict.value == "CAN"
        gate_names = [c.gate_name for c in packet.checks]
        assert "ik_feasibility" in gate_names
        # Spherical reachability should be skipped when IK passes.
        assert "reachability" not in gate_names

    def test_hard_cant_verdict_with_urdf(self) -> None:
        spec = _make_spec([5.0, 5.0, 5.0])
        packet = run_gates(spec)
        assert packet.verdict.value == "HARD_CANT"
        assert packet.failed_gate == "ik_feasibility"
        assert any(c.reason_code == "NO_IK_SOLUTION" for c in packet.checks)

    def test_spherical_fallback_without_urdf(self) -> None:
        spec = _make_spec([0.4, 0.2, 0.5], urdf=None)
        packet = run_gates(spec)
        assert packet.verdict.value == "CAN"
        gate_names = [c.gate_name for c in packet.checks]
        assert "ik_feasibility" not in gate_names
        assert "reachability" in gate_names

    def test_base_offset_applied(self) -> None:
        """Target in world coords should be offset by constructor base_pose."""
        data = {
            "task_id": "ik-offset",
            "meta": {"template": "pick_and_place"},
            "substrate": {
                "id": "widget",
                "mass_kg": 0.5,
                "initial_pose": {"xyz": [0.0, 0.0, 0.0]},
            },
            "transformation": {
                "target_pose": {"xyz": [10.4, 10.2, 0.5]},
                "tolerance_m": 0.01,
            },
            "constructor": {
                "id": "ur5e",
                "base_pose": {"xyz": [10.0, 10.0, 0.0]},
                "max_reach_m": 1.85,
                "max_payload_kg": 5.0,
                "urdf_path": _URDF,
                "base_link": "base_link",
                "ee_link": "ee_link",
            },
            "allowed_adjustments": {"can_move_target": True},
        }
        spec = TaskSpec.model_validate(data)
        packet = run_gates(spec)
        assert packet.verdict.value == "CAN"
