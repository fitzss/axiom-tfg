"""Path keepout gate: does the motion path cross a forbidden zone?

Unlike the endpoint keepout gate (which only checks the target pose),
this gate samples points along the full Cartesian path and rejects plans
whose straight-line segments pass through keepout zones.

Path construction:
    initial_pose → waypoints[0] → … → waypoints[N] → target_pose

When no waypoints are provided and an initial_pose exists, the path is a
single segment from initial_pose to target_pose.  If no initial_pose
exists, the gate is skipped (cannot check a path without a start point).
"""

from __future__ import annotations

from axiom_tfg.gates.keepout import _minimal_escape, _point_in_expanded_aabb
from axiom_tfg.models import (
    CounterfactualFix,
    FixType,
    GateResult,
    GateStatus,
    TaskSpec,
)

GATE_NAME = "path_keepout"
_SAMPLES_PER_SEGMENT = 10


def _interpolate(a: list[float], b: list[float], n: int) -> list[list[float]]:
    """Return *n* evenly-spaced points between *a* and *b* (exclusive of endpoints)."""
    points: list[list[float]] = []
    for i in range(1, n + 1):
        t = i / (n + 1)
        points.append([a[j] + t * (b[j] - a[j]) for j in range(3)])
    return points


def check_path_keepout(
    spec: TaskSpec,
) -> tuple[GateResult, list[CounterfactualFix]] | None:
    """Check whether the motion path crosses any keepout zone.

    Returns ``None`` when no initial_pose is available (gate skipped).
    Otherwise returns ``(GateResult, fixes)``.
    """
    initial = spec.substrate.initial_pose.xyz
    # Skip when initial pose is origin-default and no waypoints — means
    # the caller didn't specify a meaningful start point.  However, if
    # waypoints are present we always check.
    if initial == [0.0, 0.0, 0.0] and not spec.transformation.waypoints:
        return None

    env = spec.environment
    if not env.keepout_zones:
        return (
            GateResult(
                gate_name=GATE_NAME,
                status=GateStatus.PASS,
                measured_values={"keepout_zones_checked": 0, "segments": 0},
            ),
            [],
        )

    buffer = env.safety_buffer
    target = spec.transformation.target_pose.xyz

    # Build ordered path nodes.
    nodes: list[list[float]] = [initial]
    for wp in spec.transformation.waypoints:
        nodes.append(wp.xyz)
    nodes.append(target)

    # Sample each segment.
    for seg_idx in range(len(nodes) - 1):
        seg_start = nodes[seg_idx]
        seg_end = nodes[seg_idx + 1]
        samples = _interpolate(seg_start, seg_end, _SAMPLES_PER_SEGMENT)

        for pt_idx, point in enumerate(samples):
            for zone in env.keepout_zones:
                if _point_in_expanded_aabb(point, zone, buffer):
                    escaped, delta = _minimal_escape(point, zone, buffer)

                    measured = {
                        "segment_index": seg_idx,
                        "sample_index": pt_idx,
                        "violating_point": [round(v, 6) for v in point],
                        "violating_zone_id": zone.id,
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
                                    f"Path crosses zone '{zone.id}' at "
                                    f"{[round(v, 4) for v in point]}. "
                                    f"Nearest clear point: "
                                    f"{[round(v, 6) for v in escaped]}."
                                ),
                                proposed_patch={
                                    "projected_target_xyz": [
                                        round(v, 6) for v in escaped
                                    ],
                                },
                            )
                        )

                    return (
                        GateResult(
                            gate_name=GATE_NAME,
                            status=GateStatus.FAIL,
                            measured_values=measured,
                            reason_code="PATH_CROSSES_KEEPOUT",
                        ),
                        fixes,
                    )

    total_segments = len(nodes) - 1
    return (
        GateResult(
            gate_name=GATE_NAME,
            status=GateStatus.PASS,
            measured_values={
                "keepout_zones_checked": len(env.keepout_zones),
                "segments": total_segments,
                "samples_per_segment": _SAMPLES_PER_SEGMENT,
            },
        ),
        [],
    )
