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


GATE_NAME = "payload"


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
        fixes.append(
            CounterfactualFix(
                type=FixType.SPLIT_PAYLOAD,
                delta=round(excess, 6),
                instruction=(
                    f"Split payload into {split_count} trips of "
                    f"<= {max_payload} kg each."
                ),
                proposed_patch={
                    "suggested_payload_split_count": split_count,
                },
            )
        )

    if adj.can_change_constructor:
        fixes.append(
            CounterfactualFix(
                type=FixType.CHANGE_CONSTRUCTOR,
                delta=round(excess, 6),
                instruction=(
                    f"Replace constructor with one whose max_payload_kg >= {mass} kg."
                ),
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
