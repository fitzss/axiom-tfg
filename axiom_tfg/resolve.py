"""Closed-loop VLA resolution — validate, fix, re-plan, repeat.

The resolver orchestrates a feedback loop between a VLA (or any action
source) and the Axiom gate pipeline::

    VLA proposes → Axiom gates → fix fed back as constraint → VLA re-plans

Usage::

    from axiom_tfg import resolve, Constraint

    def my_vla(task: str, constraints: list[Constraint]) -> list[dict]:
        # Your VLA / planner logic here.
        # Use constraints to guide re-planning — each constraint has:
        #   .instruction  — human-readable fix ("move target 12cm closer")
        #   .reason       — gate reason code ("OUT_OF_REACH")
        #   .proposed_patch — structured fix ({"target_xyz": [0.26, ...]})
        return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35}]

    result = resolve(my_vla, "pick up the mug")
    if result.resolved:
        robot.execute(result.actions)
    else:
        print(f"Failed after {result.attempts} attempts")
        print(f"Last failure: {result.final_result.reason}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Union

from axiom_tfg.vla import ActionResult, PlanResult, validate_action, validate_plan


@dataclass(frozen=True)
class Constraint:
    """A physical constraint derived from a gate failure.

    Passed to the VLA callable so it can adjust its next proposal.

    Attributes:
        instruction: Human-readable fix, e.g. "Move target within 1.85 m of base"
        reason: Gate reason code, e.g. "OUT_OF_REACH", "OVER_PAYLOAD"
        fix_type: Fix category, e.g. "MOVE_TARGET", "SPLIT_PAYLOAD"
        proposed_patch: Structured fix data, e.g. ``{"target_xyz": [0.26, ...]}``
    """

    instruction: str
    reason: str
    fix_type: str | None = None
    proposed_patch: dict[str, Any] | None = None


@dataclass(frozen=True)
class Attempt:
    """Record of a single propose-then-validate cycle."""

    attempt: int
    actions: list[dict[str, Any]]
    result: Union[ActionResult, PlanResult]
    constraint_added: Constraint | None


@dataclass(frozen=True)
class ResolveResult:
    """Outcome of the closed-loop resolution."""

    resolved: bool
    actions: list[dict[str, Any]]
    attempts: int
    constraints: list[Constraint]
    history: list[Attempt]
    final_result: Union[ActionResult, PlanResult]


def _extract_constraint(
    result: Union[ActionResult, PlanResult],
) -> Constraint | None:
    """Build a Constraint from a failed gate result."""
    if isinstance(result, PlanResult):
        if result.blocked_at_step is None:
            return None
        step_result = result.steps[result.blocked_at_step]
        return _extract_constraint(step_result)

    if result.allowed or not result.fix:
        return None

    sdk = result.sdk_result
    return Constraint(
        instruction=result.fix,
        reason=result.reason or "",
        fix_type=sdk.top_fix,
        proposed_patch=sdk.top_fix_patch,
    )


#: Type alias for the VLA callable.
VLACallable = Callable[[str, list[Constraint]], Any]


def resolve(
    vla: VLACallable,
    task: str,
    *,
    max_retries: int = 3,
    robot: str = "ur5e",
    **robot_kwargs: Any,
) -> ResolveResult:
    """Closed-loop: VLA proposes → Axiom gates → fix constraint → VLA re-plans.

    Args:
        vla: Callable ``(task, constraints) -> actions``.  Must return a
            ``list[dict]`` (or a single ``dict``).  Each dict needs at
            minimum ``target_xyz``.
        task: Natural-language task description forwarded to the VLA.
        max_retries: Maximum re-planning attempts after the initial proposal.
            Total attempts = 1 + max_retries.
        robot: Robot model name (default ``"ur5e"``).
        **robot_kwargs: Forwarded to ``validate_action`` / ``validate_plan``
            (e.g. ``max_payload_kg``, ``keepout_zones``).

    Returns:
        :class:`ResolveResult` — ``resolved=True`` if a feasible plan was
        found within the retry budget.
    """
    constraints: list[Constraint] = []
    history: list[Attempt] = []
    gate_kwargs: dict[str, Any] = dict(robot=robot, **robot_kwargs)

    actions: list[dict[str, Any]] = []
    result: Union[ActionResult, PlanResult, None] = None

    for i in range(1 + max_retries):
        raw = vla(task, list(constraints))

        # Normalize single dict → list
        if isinstance(raw, dict):
            actions = [raw]
        else:
            actions = list(raw)

        # Gate
        if len(actions) == 1:
            result = validate_action(actions[0], **gate_kwargs)
        else:
            result = validate_plan(actions, **gate_kwargs)

        # Extract constraint from failure
        constraint: Constraint | None = None
        if not result.allowed:
            constraint = _extract_constraint(result)
            if constraint is not None:
                constraints.append(constraint)

        history.append(
            Attempt(
                attempt=i,
                actions=list(actions),
                result=result,
                constraint_added=constraint,
            )
        )

        if result.allowed:
            return ResolveResult(
                resolved=True,
                actions=actions,
                attempts=i + 1,
                constraints=constraints,
                history=history,
                final_result=result,
            )

        # No fix available — no point retrying
        if constraint is None:
            break

    return ResolveResult(
        resolved=False,
        actions=actions,
        attempts=len(history),
        constraints=constraints,
        history=history,
        final_result=result,  # type: ignore[arg-type]
    )
