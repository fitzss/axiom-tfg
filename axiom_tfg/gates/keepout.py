"""Keep-out zone gate: is the target pose inside a forbidden AABB?"""

from __future__ import annotations

import math

from axiom_tfg.models import (
    CounterfactualFix,
    FixType,
    GateResult,
    GateStatus,
    KeepoutZone,
    TaskSpec,
)

GATE_NAME = "keepout"


def _point_in_expanded_aabb(
    point: list[float],
    zone: KeepoutZone,
    buffer: float,
) -> bool:
    """Return True if *point* is inside the AABB expanded by *buffer*."""
    for i in range(3):
        if point[i] < zone.min_xyz[i] - buffer:
            return False
        if point[i] > zone.max_xyz[i] + buffer:
            return False
    return True


def _minimal_escape(
    point: list[float],
    zone: KeepoutZone,
    buffer: float,
) -> tuple[list[float], float]:
    """Compute the smallest axis-aligned translation that moves *point* to
    just outside the expanded AABB.

    Returns (escaped_point, L2_distance).
    """
    best_dist = math.inf
    best_point: list[float] = list(point)

    for i in range(3):
        lo = zone.min_xyz[i] - buffer
        hi = zone.max_xyz[i] + buffer

        # Distance to the low face (move in -axis direction)
        d_lo = point[i] - lo
        if 0 <= d_lo < best_dist:
            candidate = list(point)
            candidate[i] = lo
            best_dist = d_lo
            best_point = candidate

        # Distance to the high face (move in +axis direction)
        d_hi = hi - point[i]
        if 0 <= d_hi < best_dist:
            candidate = list(point)
            candidate[i] = hi
            best_dist = d_hi
            best_point = candidate

    return best_point, best_dist


def check_keepout(spec: TaskSpec) -> tuple[GateResult, list[CounterfactualFix]]:
    """Check whether the target pose falls inside any keepout zone.

    Returns a (GateResult, fixes) tuple.
    """
    target = spec.transformation.target_pose.xyz
    env = spec.environment
    buffer = env.safety_buffer

    # If no zones defined, pass trivially.
    if not env.keepout_zones:
        return (
            GateResult(
                gate_name=GATE_NAME,
                status=GateStatus.PASS,
                measured_values={"keepout_zones_checked": 0},
            ),
            [],
        )

    for zone in env.keepout_zones:
        if _point_in_expanded_aabb(target, zone, buffer):
            escaped, delta = _minimal_escape(target, zone, buffer)
            measured = {
                "violating_zone_id": zone.id,
                "target_xyz": target,
                "zone_min_xyz": zone.min_xyz,
                "zone_max_xyz": zone.max_xyz,
                "safety_buffer_m": buffer,
                "escape_delta_m": round(delta, 6),
            }

            fixes: list[CounterfactualFix] = []
            if spec.allowed_adjustments.can_move_target:
                fixes.append(
                    CounterfactualFix(
                        type=FixType.MOVE_TARGET,
                        delta=round(delta, 6),
                        instruction=(
                            f"Move target {delta:.4f} m to exit keepout zone "
                            f"'{zone.id}' (including {buffer} m safety buffer)."
                        ),
                        proposed_patch={
                            "projected_target_xyz": [round(v, 6) for v in escaped],
                        },
                    )
                )

            return (
                GateResult(
                    gate_name=GATE_NAME,
                    status=GateStatus.FAIL,
                    measured_values=measured,
                    reason_code="IN_KEEP_OUT_ZONE",
                ),
                fixes,
            )

    return (
        GateResult(
            gate_name=GATE_NAME,
            status=GateStatus.PASS,
            measured_values={
                "keepout_zones_checked": len(env.keepout_zones),
            },
        ),
        [],
    )
