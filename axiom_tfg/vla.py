"""VLA integration layer — gate VLA-proposed actions before execution.

Adapts Vision-Language-Action model outputs into TaskSpec feasibility
checks using the existing axiom-tfg gate pipeline.

Usage::

    from axiom_tfg.vla import validate_action, validate_plan

    # Gate a single VLA-proposed action
    result = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35})
    if result.allowed:
        robot.execute(action)

    # Gate a multi-step plan — fails fast at first blocked step
    plan_result = validate_plan([
        {"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35},
        {"target_xyz": [0.8, -0.1, 0.6], "mass_kg": 0.35},
    ])
    if plan_result.allowed:
        robot.execute_plan(actions)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from axiom_tfg.sdk import Result, check_simple


@dataclass(frozen=True)
class ActionResult:
    """Result of gating a single VLA action."""

    allowed: bool
    verdict: str
    reason: str | None
    fix: str | None
    evidence: dict[str, Any]
    sdk_result: Result


@dataclass(frozen=True)
class PlanResult:
    """Result of gating a VLA action sequence."""

    allowed: bool
    blocked_at_step: int | None
    reason: str | None
    fix: str | None
    steps: list[ActionResult]


def validate_action(
    action: dict[str, Any],
    *,
    robot: str = "ur5e",
    urdf_path: str | None = None,
    base_xyz: list[float] | None = None,
    max_reach_m: float = 1.85,
    max_payload_kg: float = 5.0,
    keepout_zones: list[dict[str, Any]] | None = None,
) -> ActionResult:
    """Gate a single VLA-proposed action before execution.

    The ``action`` dict is expected to contain at minimum:

    - ``target_xyz`` — ``[x, y, z]`` end-effector target (required)
    - ``mass_kg`` — object mass in kg (default 0.5)
    - ``target_rpy_rad`` — ``[r, p, y]`` orientation (optional)
    - ``target_quat_wxyz`` — ``[w, x, y, z]`` quaternion (optional)

    All other gate parameters (robot, keepout zones, etc.) are passed
    through as keyword arguments.
    """
    result = check_simple(
        target_xyz=action["target_xyz"],
        target_rpy_rad=action.get("target_rpy_rad"),
        target_quat_wxyz=action.get("target_quat_wxyz"),
        mass_kg=action.get("mass_kg", 0.5),
        robot=robot,
        urdf_path=urdf_path,
        base_xyz=base_xyz,
        max_reach_m=max_reach_m,
        max_payload_kg=max_payload_kg,
        keepout_zones=keepout_zones,
    )

    return ActionResult(
        allowed=result.verdict == "CAN",
        verdict=result.verdict,
        reason=result.reason_code,
        fix=result.top_fix_instruction,
        evidence=result.evidence,
        sdk_result=result,
    )


def validate_plan(
    actions: list[dict[str, Any]],
    *,
    robot: str = "ur5e",
    urdf_path: str | None = None,
    base_xyz: list[float] | None = None,
    max_reach_m: float = 1.85,
    max_payload_kg: float = 5.0,
    keepout_zones: list[dict[str, Any]] | None = None,
) -> PlanResult:
    """Gate a sequence of VLA actions.  Stops at the first blocked step.

    Returns a :class:`PlanResult` with per-step evidence.  If every step
    passes, ``allowed`` is ``True``.  If any step is blocked, ``allowed``
    is ``False`` and ``blocked_at_step`` / ``reason`` / ``fix`` describe
    the first failure.
    """
    robot_kwargs: dict[str, Any] = dict(
        robot=robot,
        urdf_path=urdf_path,
        base_xyz=base_xyz,
        max_reach_m=max_reach_m,
        max_payload_kg=max_payload_kg,
        keepout_zones=keepout_zones,
    )

    steps: list[ActionResult] = []
    for action in actions:
        r = validate_action(action, **robot_kwargs)
        steps.append(r)
        if not r.allowed:
            return PlanResult(
                allowed=False,
                blocked_at_step=len(steps) - 1,
                reason=r.reason,
                fix=r.fix,
                steps=steps,
            )

    return PlanResult(
        allowed=True,
        blocked_at_step=None,
        reason=None,
        fix=None,
        steps=steps,
    )
