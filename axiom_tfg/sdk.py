"""Public Python SDK for axiom-tfg — one-liner feasibility checks.

Usage::

    from axiom_tfg import check, check_simple

    # Quick check with keyword args:
    result = check_simple(
        target_xyz=[0.4, 0.2, 0.5],
        robot="ur5e",
        mass_kg=0.35,
    )
    print(result.verdict)  # "CAN"

    # Full-control check with a TaskSpec:
    from axiom_tfg.models import TaskSpec
    spec = TaskSpec.model_validate(yaml_dict)
    result = check(spec)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from axiom_tfg.evidence import run_gates
from axiom_tfg.models import EvidencePacket, TaskSpec
from axiom_tfg.robots import ROBOT_REGISTRY


@dataclass(frozen=True)
class Result:
    """Immutable result of a feasibility check."""

    verdict: str
    failed_gate: str | None
    reason_code: str | None
    top_fix: str | None
    top_fix_instruction: str | None
    top_fix_patch: dict[str, Any] | None
    evidence: dict[str, Any]
    validation_level_reached: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "failed_gate": self.failed_gate,
            "reason_code": self.reason_code,
            "top_fix": self.top_fix,
            "top_fix_instruction": self.top_fix_instruction,
            "top_fix_patch": self.top_fix_patch,
            "validation_level_reached": self.validation_level_reached,
            "evidence": self.evidence,
        }


def _packet_to_result(packet: EvidencePacket) -> Result:
    """Convert an EvidencePacket to a Result."""
    reason_code: str | None = None
    for c in packet.checks:
        if c.reason_code:
            reason_code = c.reason_code
            break

    top_fix: str | None = None
    top_fix_instruction: str | None = None
    top_fix_patch: dict[str, Any] | None = None
    if packet.counterfactual_fixes:
        f = packet.counterfactual_fixes[0]
        top_fix = f.type.value
        top_fix_instruction = f.instruction
        top_fix_patch = f.proposed_patch

    return Result(
        verdict=packet.verdict.value,
        failed_gate=packet.failed_gate,
        reason_code=reason_code,
        top_fix=top_fix,
        top_fix_instruction=top_fix_instruction,
        top_fix_patch=top_fix_patch,
        evidence=packet.model_dump(mode="json"),
        validation_level_reached=packet.validation_level_reached,
    )


def check(spec: TaskSpec) -> Result:
    """Run the full gate pipeline on a validated TaskSpec and return a Result."""
    packet = run_gates(spec)
    return _packet_to_result(packet)


_SENTINEL = object()


def check_simple(
    *,
    target_xyz: list[float],
    target_rpy_rad: list[float] | None = None,
    target_quat_wxyz: list[float] | None = None,
    orientation_tolerance_rad: float | None = None,
    robot: str = "ur5e",
    urdf_path: str | None = None,
    base_link: object = _SENTINEL,
    ee_link: object = _SENTINEL,
    base_xyz: list[float] | None = None,
    max_reach_m: object = _SENTINEL,
    max_payload_kg: object = _SENTINEL,
    mass_kg: float = 0.5,
    tolerance_m: float = 0.01,
    task_id: str | None = None,
    keepout_zones: list[dict[str, Any]] | None = None,
    can_move_target: bool = True,
    can_change_constructor: bool = True,
    can_split_payload: bool = False,
) -> Result:
    """Run feasibility gates with keyword arguments — no YAML needed.

    If ``robot`` matches a registry entry, ``max_reach_m``, ``max_payload_kg``,
    ``base_link``, ``ee_link``, and ``urdf_path`` are auto-populated from the
    profile.  Explicit kwargs still override.
    """
    profile = ROBOT_REGISTRY.get(robot)

    if urdf_path is None and profile is not None:
        urdf_path = profile.urdf_path

    resolved_base_link: str | None
    if base_link is not _SENTINEL:
        resolved_base_link = base_link  # type: ignore[assignment]
    elif profile is not None:
        resolved_base_link = profile.base_link
    else:
        resolved_base_link = "base_link"

    resolved_ee_link: str | None
    if ee_link is not _SENTINEL:
        resolved_ee_link = ee_link  # type: ignore[assignment]
    elif profile is not None:
        resolved_ee_link = profile.ee_link
    else:
        resolved_ee_link = "ee_link"

    resolved_reach: float
    if max_reach_m is not _SENTINEL:
        resolved_reach = max_reach_m  # type: ignore[assignment]
    elif profile is not None:
        resolved_reach = profile.max_reach_m
    else:
        resolved_reach = 1.85

    resolved_payload: float
    if max_payload_kg is not _SENTINEL:
        resolved_payload = max_payload_kg  # type: ignore[assignment]
    elif profile is not None:
        resolved_payload = profile.max_payload_kg
    else:
        resolved_payload = 5.0

    data: dict[str, Any] = {
        "task_id": task_id or "sdk-check",
        "meta": {"template": "pick_and_place"},
        "substrate": {
            "id": "object",
            "mass_kg": mass_kg,
            "initial_pose": {"xyz": [0.0, 0.0, 0.0]},
        },
        "transformation": {
            "target_pose": {"xyz": target_xyz},
            "tolerance_m": tolerance_m,
        },
        "constructor": {
            "id": robot,
            "base_pose": {"xyz": base_xyz or [0.0, 0.0, 0.0]},
            "max_reach_m": resolved_reach,
            "max_payload_kg": resolved_payload,
            "urdf_path": urdf_path,
            "base_link": resolved_base_link,
            "ee_link": resolved_ee_link,
        },
        "allowed_adjustments": {
            "can_move_target": can_move_target,
            "can_change_constructor": can_change_constructor,
            "can_split_payload": can_split_payload,
        },
    }

    if target_quat_wxyz is not None:
        data["transformation"]["target_quat_wxyz"] = target_quat_wxyz
    elif target_rpy_rad is not None:
        data["transformation"]["target_rpy_rad"] = target_rpy_rad

    if orientation_tolerance_rad is not None:
        data["transformation"]["orientation_tolerance_rad"] = orientation_tolerance_rad

    if keepout_zones:
        data["environment"] = {"keepout_zones": keepout_zones}

    spec = TaskSpec.model_validate(data)
    return check(spec)
