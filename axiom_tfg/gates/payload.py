"""Payload gate: can the constructor carry the substrate?"""

from __future__ import annotations

import math

from axiom_tfg.models import (
    CounterfactualFix,
    FixType,
    GateResult,
    GateStatus,
    TaskSpec,
)
from axiom_tfg.robots import ROBOT_REGISTRY


GATE_NAME = "payload"


def _compute_staging_positions(
    target_xyz: list[float],
    split_count: int,
) -> list[list[float]]:
    """Compute staging positions for a payload split.

    Places intermediate staging points in a line between the robot base
    (origin) and the target, at table height (z=0.1m).  The final position
    is the original target.
    """
    positions: list[list[float]] = []
    # Spread staging points in a small arc near the target so they don't overlap.
    tx, ty, tz = target_xyz
    for i in range(split_count):
        frac = (i + 1) / split_count
        # Offset each staging point slightly in y so they don't stack.
        y_offset = 0.15 * (i - (split_count - 1) / 2)
        positions.append([
            round(tx * frac * 0.8, 4),
            round(ty * frac + y_offset, 4),
            round(max(tz, 0.1), 4),
        ])
    # Last position is always the actual target.
    positions[-1] = [round(v, 4) for v in target_xyz]
    return positions


def check_payload(spec: TaskSpec) -> tuple[GateResult, list[CounterfactualFix]]:
    """Check whether substrate mass is within the constructor's payload limit.

    Returns a (GateResult, fixes) tuple.
    """
    mass = spec.substrate.mass_kg
    max_payload = spec.constructor.max_payload_kg

    measured = {
        "mass_kg": mass,
        "max_payload_kg": max_payload,
    }

    if mass <= max_payload:
        return (
            GateResult(
                gate_name=GATE_NAME,
                status=GateStatus.PASS,
                measured_values=measured,
            ),
            [],
        )

    # ── FAIL path ──────────────────────────────────────────────────────
    excess = mass - max_payload
    fixes: list[CounterfactualFix] = []
    adj = spec.allowed_adjustments

    if adj.can_split_payload:
        split_count = math.ceil(mass / max_payload)
        split_mass = round(mass / split_count, 2)
        target_xyz = spec.transformation.target_pose.xyz
        staging = _compute_staging_positions(target_xyz, split_count)

        fixes.append(
            CounterfactualFix(
                type=FixType.SPLIT_PAYLOAD,
                delta=round(excess, 6),
                instruction=(
                    f"Object mass {mass} kg exceeds payload limit {max_payload} kg. "
                    f"Split into {split_count} sequential lifts of {split_mass} kg each. "
                    f"Each lift carries a portion to the target. "
                    f"Use mass_kg={split_mass} for each action."
                ),
                proposed_patch={
                    "suggested_payload_split_count": split_count,
                    "split_mass_kg": split_mass,
                    "staging_positions": staging,
                },
            )
        )

    if adj.can_change_constructor:
        # Find robots in the registry that can handle this payload.
        capable = sorted(
            [
                (name, p.max_payload_kg)
                for name, p in ROBOT_REGISTRY.items()
                if p.max_payload_kg >= mass and name != spec.constructor.id
            ],
            key=lambda x: x[1],
        )
        if capable:
            suggestions = ", ".join(
                f"{name} ({cap}kg)" for name, cap in capable
            )
            instruction = (
                f"Object mass {mass} kg exceeds this robot's payload "
                f"limit of {max_payload} kg. "
                f"Robots that can handle this: {suggestions}."
            )
        else:
            instruction = (
                f"Object mass {mass} kg exceeds payload limit "
                f"{max_payload} kg. No robot in registry can handle this."
            )
        fixes.append(
            CounterfactualFix(
                type=FixType.CHANGE_CONSTRUCTOR,
                delta=round(excess, 6),
                instruction=instruction,
                proposed_patch={
                    "capable_robots": [name for name, _ in capable],
                } if capable else None,
            )
        )

    return (
        GateResult(
            gate_name=GATE_NAME,
            status=GateStatus.FAIL,
            measured_values=measured,
            reason_code="OVER_PAYLOAD",
        ),
        fixes,
    )
