"""Reachability gate: is the target within the constructor's reach sphere?"""

from __future__ import annotations

from axiom_tfg.models import (
    CounterfactualFix,
    FixType,
    GateResult,
    GateStatus,
    TaskSpec,
)
from axiom_tfg.utils import euclidean_distance, point_toward, project_onto_sphere


GATE_NAME = "reachability"


def check_reachability(spec: TaskSpec) -> tuple[GateResult, list[CounterfactualFix]]:
    """Check whether the target pose is within reach of the constructor.

    Returns a (GateResult, fixes) tuple.  *fixes* is non-empty only when the
    gate fails and allowed_adjustments permit a counterfactual remedy.
    """
    base = spec.constructor.base_pose.xyz
    target = spec.transformation.target_pose.xyz
    max_reach = spec.constructor.max_reach_m

    distance = euclidean_distance(base, target)

    measured = {
        "distance_m": round(distance, 6),
        "max_reach_m": max_reach,
    }

    if distance <= max_reach:
        return (
            GateResult(
                gate_name=GATE_NAME,
                status=GateStatus.PASS,
                measured_values=measured,
            ),
            [],
        )

    # ── FAIL path ──────────────────────────────────────────────────────
    overshoot = distance - max_reach
    fixes: list[CounterfactualFix] = []
    adj = spec.allowed_adjustments

    if adj.can_move_target:
        projected = project_onto_sphere(base, target, max_reach)
        fixes.append(
            CounterfactualFix(
                type=FixType.MOVE_TARGET,
                delta=round(overshoot, 6),
                instruction=(
                    f"Move target {overshoot:.4f} m closer to base "
                    f"(projected onto reach sphere)."
                ),
                proposed_patch={
                    "projected_target_xyz": [round(v, 6) for v in projected],
                },
            )
        )

    if adj.can_move_base:
        new_base = point_toward(base, target, overshoot)
        fixes.append(
            CounterfactualFix(
                type=FixType.MOVE_BASE,
                delta=round(overshoot, 6),
                instruction=(
                    f"Move constructor base {overshoot:.4f} m toward target."
                ),
                proposed_patch={
                    "suggested_base_xyz": [round(v, 6) for v in new_base],
                },
            )
        )

    if adj.can_change_constructor:
        fixes.append(
            CounterfactualFix(
                type=FixType.CHANGE_CONSTRUCTOR,
                delta=round(overshoot, 6),
                instruction=(
                    f"Replace constructor with one whose max_reach_m >= {distance:.4f} m."
                ),
            )
        )

    return (
        GateResult(
            gate_name=GATE_NAME,
            status=GateStatus.FAIL,
            measured_values=measured,
            reason_code="OUT_OF_REACH",
        ),
        fixes,
    )
