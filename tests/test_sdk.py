"""Tests for the axiom_tfg Python SDK (check / check_simple)."""

from __future__ import annotations

import math
from pathlib import Path

from axiom_tfg import Result, check, check_simple
from axiom_tfg.models import TaskSpec
from axiom_tfg.runner import run_taskspec

_URDF = str(Path(__file__).resolve().parent.parent / "axiom_tfg" / "data" / "ur5e.urdf")


class TestCheckSimple:
    """check_simple convenience API."""

    def test_can_for_reachable_target(self) -> None:
        result = check_simple(target_xyz=[0.4, 0.2, 0.5])
        assert result.verdict == "CAN"
        assert result.failed_gate is None
        assert result.reason_code is None

    def test_hard_cant_for_unreachable_target(self) -> None:
        result = check_simple(target_xyz=[5.0, 5.0, 5.0])
        assert result.verdict == "HARD_CANT"
        assert result.failed_gate == "ik_feasibility"
        assert result.reason_code == "NO_IK_SOLUTION"
        assert result.top_fix is not None
        assert result.top_fix_instruction is not None

    def test_hard_cant_payload(self) -> None:
        result = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=100.0)
        assert result.verdict == "HARD_CANT"
        assert result.failed_gate == "payload"

    def test_with_orientation(self) -> None:
        result = check_simple(
            target_xyz=[0.4, 0.2, 0.5],
            target_rpy_rad=[0.0, math.pi, 0.0],
        )
        assert result.verdict == "CAN"

    def test_with_keepout_zones(self) -> None:
        result = check_simple(
            target_xyz=[0.5, 0.5, 0.5],
            keepout_zones=[
                {"id": "box", "min_xyz": [0.3, 0.3, 0.0], "max_xyz": [0.7, 0.7, 1.0]}
            ],
        )
        assert result.verdict == "HARD_CANT"
        assert result.failed_gate == "keepout"

    def test_bundled_urdf_used_by_default(self) -> None:
        """When robot='ur5e' and no urdf_path, bundled URDF is used."""
        result = check_simple(target_xyz=[0.4, 0.2, 0.5], robot="ur5e")
        assert result.verdict == "CAN"
        # IK gate should have run (not spherical fallback).
        ik_checks = [c for c in result.evidence["checks"] if c["gate_name"] == "ik_feasibility"]
        assert len(ik_checks) == 1
        assert ik_checks[0]["measured_values"]["solver"] == "ikpy"

    def test_to_dict(self) -> None:
        result = check_simple(target_xyz=[0.4, 0.2, 0.5])
        d = result.to_dict()
        assert d["verdict"] == "CAN"
        assert "evidence" in d
        assert "checks" in d["evidence"]


class TestCheck:
    """check() with a full TaskSpec."""

    def test_same_verdict_as_runner(self) -> None:
        """check() should produce the same verdict as the internal runner."""
        data = {
            "task_id": "sdk-vs-runner",
            "meta": {"template": "pick_and_place"},
            "substrate": {
                "id": "widget",
                "mass_kg": 0.5,
                "initial_pose": {"xyz": [0.0, 0.0, 0.0]},
            },
            "transformation": {
                "target_pose": {"xyz": [0.4, 0.2, 0.5]},
                "tolerance_m": 0.01,
            },
            "constructor": {
                "id": "ur5e",
                "base_pose": {"xyz": [0.0, 0.0, 0.0]},
                "max_reach_m": 1.85,
                "max_payload_kg": 5.0,
                "urdf_path": _URDF,
                "base_link": "base_link",
                "ee_link": "ee_link",
            },
            "allowed_adjustments": {"can_move_target": True},
        }
        spec = TaskSpec.model_validate(data)

        sdk_result = check(spec)
        runner_result, _packet = run_taskspec(spec)

        assert sdk_result.verdict == runner_result["verdict"]
        assert sdk_result.failed_gate == runner_result["failed_gate"]
        assert sdk_result.reason_code == runner_result["reason_code"]

    def test_result_is_frozen(self) -> None:
        result = check_simple(target_xyz=[0.4, 0.2, 0.5])
        try:
            result.verdict = "SOMETHING"  # type: ignore[misc]
            assert False, "Should have raised"
        except AttributeError:
            pass  # Expected — frozen dataclass


class TestImports:
    """SDK is importable from the top-level package."""

    def test_import_check(self) -> None:
        from axiom_tfg import check
        assert callable(check)

    def test_import_check_simple(self) -> None:
        from axiom_tfg import check_simple
        assert callable(check_simple)

    def test_import_result(self) -> None:
        from axiom_tfg import Result
        assert Result is not None
