"""Tests for the CNC machine tending demo scenario."""

from __future__ import annotations

from axiom_tfg.demo_scenario import run_demo


def test_mock_scenario_resolves() -> None:
    """The mock VLA scenario resolves in exactly 4 attempts."""
    result = run_demo(live=False)
    assert result.resolved
    assert result.attempts == 4


def test_mock_scenario_hits_three_gates() -> None:
    """History shows IK, keepout, and payload failures in that order."""
    result = run_demo(live=False)
    reasons = [
        a.constraint_added.reason
        for a in result.history
        if a.constraint_added is not None
    ]
    assert len(reasons) == 3
    # Attempt 0: IK feasibility gate
    assert reasons[0] == "NO_IK_SOLUTION"
    # Attempt 1: keepout zone gate
    assert reasons[1] == "IN_KEEP_OUT_ZONE"
    # Attempt 2: payload gate
    assert reasons[2] == "OVER_PAYLOAD"


def test_mock_scenario_final_plan_valid() -> None:
    """All final actions pass validation independently."""
    result = run_demo(live=False)
    assert result.resolved
    assert result.final_result.allowed
    # Final plan should have 4 actions (original 2 + payload split into 2)
    assert len(result.actions) == 4


def test_mock_vla_reads_patches() -> None:
    """The mock VLA actually uses proposed_patch coordinates (not hardcoded)."""
    result = run_demo(live=False)

    # Constraint 0: IK fix for action 0
    c0 = result.constraints[0]
    assert c0.proposed_patch is not None
    ik_fix_xyz = c0.proposed_patch["projected_target_xyz"]

    # Attempt 1 should use the IK-fixed coordinates for action 0
    attempt_1_action_0 = result.history[1].actions[0]
    assert attempt_1_action_0["target_xyz"] == ik_fix_xyz

    # Constraint 1: keepout fix for action 1
    c1 = result.constraints[1]
    assert c1.proposed_patch is not None
    keepout_fix_xyz = c1.proposed_patch["projected_target_xyz"]

    # Attempt 2 should use the keepout-fixed coordinates for action 1
    attempt_2_action_1 = result.history[2].actions[1]
    assert attempt_2_action_1["target_xyz"] == keepout_fix_xyz
