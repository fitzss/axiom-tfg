"""Tests for the VLA integration layer."""

from __future__ import annotations

import math

from axiom_tfg.vla import ActionResult, PlanResult, validate_action, validate_plan


class TestValidateAction:
    """validate_action — single VLA action gating."""

    def test_reachable_action_allowed(self) -> None:
        r = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35})
        assert r.allowed is True
        assert r.verdict == "CAN"
        assert r.reason is None
        assert r.fix is None

    def test_unreachable_action_blocked(self) -> None:
        r = validate_action({"target_xyz": [5.0, 5.0, 5.0]})
        assert r.allowed is False
        assert r.verdict == "HARD_CANT"
        assert r.reason is not None
        assert r.fix is not None

    def test_overweight_action_blocked(self) -> None:
        r = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 100.0})
        assert r.allowed is False
        assert r.reason == "OVER_PAYLOAD"

    def test_orientation_passed_through(self) -> None:
        r = validate_action({
            "target_xyz": [0.4, 0.2, 0.5],
            "target_rpy_rad": [0.0, math.pi, 0.0],
            "mass_kg": 0.35,
        })
        assert r.allowed is True

    def test_keepout_zone_respected(self) -> None:
        r = validate_action(
            {"target_xyz": [0.5, 0.5, 0.5]},
            keepout_zones=[
                {"id": "box", "min_xyz": [0.3, 0.3, 0.0], "max_xyz": [0.7, 0.7, 1.0]},
            ],
        )
        assert r.allowed is False
        assert r.reason == "IN_KEEP_OUT_ZONE"

    def test_default_mass_used(self) -> None:
        """When mass_kg is omitted, default 0.5 kg is used (well within UR5e payload)."""
        r = validate_action({"target_xyz": [0.4, 0.2, 0.5]})
        assert r.allowed is True

    def test_sdk_result_attached(self) -> None:
        r = validate_action({"target_xyz": [0.4, 0.2, 0.5]})
        assert r.sdk_result.verdict == "CAN"
        assert "checks" in r.evidence

    def test_result_is_frozen(self) -> None:
        r = validate_action({"target_xyz": [0.4, 0.2, 0.5]})
        try:
            r.allowed = False  # type: ignore[misc]
            assert False, "Should have raised"
        except AttributeError:
            pass


class TestValidatePlan:
    """validate_plan — multi-step VLA plan gating."""

    def test_all_reachable_plan_allowed(self) -> None:
        plan = [
            {"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35},
            {"target_xyz": [0.3, -0.1, 0.6], "mass_kg": 0.35},
        ]
        r = validate_plan(plan)
        assert r.allowed is True
        assert r.blocked_at_step is None
        assert len(r.steps) == 2
        assert all(s.allowed for s in r.steps)

    def test_blocked_at_second_step(self) -> None:
        plan = [
            {"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35},
            {"target_xyz": [5.0, 5.0, 5.0], "mass_kg": 0.35},
            {"target_xyz": [0.3, 0.1, 0.4], "mass_kg": 0.35},
        ]
        r = validate_plan(plan)
        assert r.allowed is False
        assert r.blocked_at_step == 1
        assert r.reason is not None
        assert r.fix is not None
        # Only 2 steps evaluated (fail-fast)
        assert len(r.steps) == 2

    def test_blocked_at_first_step(self) -> None:
        plan = [
            {"target_xyz": [5.0, 5.0, 5.0]},
            {"target_xyz": [0.4, 0.2, 0.5]},
        ]
        r = validate_plan(plan)
        assert r.allowed is False
        assert r.blocked_at_step == 0
        assert len(r.steps) == 1

    def test_empty_plan_allowed(self) -> None:
        r = validate_plan([])
        assert r.allowed is True
        assert r.blocked_at_step is None
        assert len(r.steps) == 0

    def test_single_step_plan(self) -> None:
        r = validate_plan([{"target_xyz": [0.4, 0.2, 0.5]}])
        assert r.allowed is True
        assert len(r.steps) == 1

    def test_robot_kwargs_passed_through(self) -> None:
        r = validate_plan(
            [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 3.0}],
            max_payload_kg=1.0,
        )
        assert r.allowed is False
        assert r.reason == "OVER_PAYLOAD"


class TestLockedFields:
    """locked_fields suppresses corresponding fixes."""

    def test_locked_target_suppresses_move_fix(self) -> None:
        """Unreachable target with locked target_xyz → no MOVE_TARGET fix."""
        r = validate_action({
            "target_xyz": [5.0, 5.0, 5.0],
            "mass_kg": 0.35,
            "locked_fields": ["target_xyz"],
        })
        assert r.allowed is False
        # Fix should NOT be MOVE_TARGET (target is locked)
        if r.sdk_result.top_fix is not None:
            assert r.sdk_result.top_fix != "MOVE_TARGET"

    def test_locked_mass_suppresses_split_fix(self) -> None:
        """Over-payload with locked mass_kg → no SPLIT_PAYLOAD fix."""
        r = validate_action({
            "target_xyz": [0.4, 0.2, 0.5],
            "mass_kg": 100.0,
            "is_splittable": True,
            "locked_fields": ["mass_kg"],
        })
        assert r.allowed is False
        assert r.reason == "OVER_PAYLOAD"
        # Even though is_splittable=True, mass_kg lock suppresses SPLIT_PAYLOAD
        fixes = r.sdk_result.evidence.get("counterfactual_fixes", [])
        fix_types = [f["type"] for f in fixes]
        assert "SPLIT_PAYLOAD" not in fix_types

    def test_locked_constructor_suppresses_change_fix(self) -> None:
        """Unreachable target with locked constructor → no CHANGE_CONSTRUCTOR fix."""
        r = validate_action({
            "target_xyz": [5.0, 5.0, 5.0],
            "mass_kg": 0.35,
            "locked_fields": ["constructor"],
        })
        assert r.allowed is False
        fixes = r.sdk_result.evidence.get("counterfactual_fixes", [])
        fix_types = [f["type"] for f in fixes]
        assert "CHANGE_CONSTRUCTOR" not in fix_types

    def test_no_locked_fields_preserves_behavior(self) -> None:
        """Absent locked_fields → fixes still proposed (backward compat)."""
        r = validate_action({
            "target_xyz": [5.0, 5.0, 5.0],
            "mass_kg": 0.35,
        })
        assert r.allowed is False
        assert r.fix is not None  # a fix is proposed


class TestImports:
    """VLA adapter is importable from the top-level package."""

    def test_import_validate_action(self) -> None:
        from axiom_tfg import validate_action
        assert callable(validate_action)

    def test_import_validate_plan(self) -> None:
        from axiom_tfg import validate_plan
        assert callable(validate_plan)

    def test_import_action_result(self) -> None:
        from axiom_tfg import ActionResult
        assert ActionResult is not None

    def test_import_plan_result(self) -> None:
        from axiom_tfg import PlanResult
        assert PlanResult is not None
