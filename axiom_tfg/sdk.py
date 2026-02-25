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

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiom_tfg.evidence import run_gates
from axiom_tfg.models import EvidencePacket, TaskSpec


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "failed_gate": self.failed_gate,
            "reason_code": self.reason_code,
            "top_fix": self.top_fix,
            "top_fix_instruction": self.top_fix_instruction,
            "top_fix_patch": self.top_fix_patch,
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
    )


def check(spec: TaskSpec) -> Result:
    """Run the full gate pipeline on a validated TaskSpec and return a Result."""
    packet = run_gates(spec)
    return _packet_to_result(packet)


# ── Bundled URDF path ─────────────────────────────────────────────────────

_BUNDLED_URDFS: dict[str, str] = {
    "ur5e": str(Path(__file__).resolve().parent / "data" / "ur5e.urdf"),
}


def check_simple(
    *,
    target_xyz: list[float],
    target_rpy_rad: list[float] | None = None,
    target_quat_wxyz: list[float] | None = None,
    orientation_tolerance_rad: float | None = None,
    robot: str = "ur5e",
    urdf_path: str | None = None,
    base_link: str | None = "base_link",
    ee_link: str | None = "ee_link",
    base_xyz: list[float] | None = None,
    max_reach_m: float = 1.85,
    max_payload_kg: float = 5.0,
    mass_kg: float = 0.5,
    tolerance_m: float = 0.01,
    task_id: str | None = None,
    keepout_zones: list[dict[str, Any]] | None = None,
    can_move_target: bool = True,
    can_change_constructor: bool = True,
) -> Result:
    """Run feasibility gates with keyword arguments — no YAML needed.

    If ``urdf_path`` is not provided and ``robot`` matches a bundled URDF
    (currently ``"ur5e"``), the bundled file is used automatically.
    """
    if urdf_path is None:
        urdf_path = _BUNDLED_URDFS.get(robot)

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
            "max_reach_m": max_reach_m,
            "max_payload_kg": max_payload_kg,
            "urdf_path": urdf_path,
            "base_link": base_link,
            "ee_link": ee_link,
        },
        "allowed_adjustments": {
            "can_move_target": can_move_target,
            "can_change_constructor": can_change_constructor,
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
