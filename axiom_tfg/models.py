"""Pydantic data models for TaskSpec input and EvidencePacket output."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────

class Verdict(str, Enum):
    CAN = "CAN"
    HARD_CANT = "HARD_CANT"


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class FixType(str, Enum):
    MOVE_TARGET = "MOVE_TARGET"
    MOVE_BASE = "MOVE_BASE"
    CHANGE_CONSTRUCTOR = "CHANGE_CONSTRUCTOR"
    SPLIT_PAYLOAD = "SPLIT_PAYLOAD"


# ── TaskSpec (input YAML) ──────────────────────────────────────────────────

class XYZ(BaseModel):
    """A 3-D coordinate."""
    xyz: list[float] = Field(min_length=3, max_length=3)


class SubstrateSpec(BaseModel):
    id: str
    mass_kg: float = Field(gt=0)
    initial_pose: XYZ


def _rpy_to_quat(roll: float, pitch: float, yaw: float) -> list[float]:
    """Convert roll/pitch/yaw (radians) to quaternion [w, x, y, z]."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return [
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ]


class TransformationSpec(BaseModel):
    target_pose: XYZ
    tolerance_m: float = Field(gt=0)
    target_quat_wxyz: list[float] | None = Field(
        default=None, min_length=4, max_length=4,
    )
    target_rpy_rad: list[float] | None = Field(
        default=None, min_length=3, max_length=3,
    )
    orientation_tolerance_rad: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _normalise_orientation(self) -> "TransformationSpec":
        """Normalise orientation: quaternion wins over RPY; RPY is converted."""
        if self.target_quat_wxyz is not None:
            # Normalise the quaternion to unit length.
            n = math.sqrt(sum(c * c for c in self.target_quat_wxyz))
            if n > 0:
                self.target_quat_wxyz = [c / n for c in self.target_quat_wxyz]
        elif self.target_rpy_rad is not None:
            self.target_quat_wxyz = _rpy_to_quat(*self.target_rpy_rad)
        return self


class ConstructorSpec(BaseModel):
    id: str
    base_pose: XYZ
    max_reach_m: float = Field(gt=0)
    max_payload_kg: float = Field(gt=0)
    urdf_path: str | None = None
    base_link: str | None = None
    ee_link: str | None = None


class AllowedAdjustments(BaseModel):
    can_move_target: bool = False
    can_move_base: bool = False
    can_change_constructor: bool = False
    can_split_payload: bool = False


class KeepoutZone(BaseModel):
    """Axis-aligned bounding box defining a forbidden volume."""
    id: str
    min_xyz: list[float] = Field(min_length=3, max_length=3)
    max_xyz: list[float] = Field(min_length=3, max_length=3)


class EnvironmentSpec(BaseModel):
    keepout_zones: list[KeepoutZone] = Field(default_factory=list)
    safety_buffer: float = Field(default=0.02, ge=0)


class MetaSpec(BaseModel):
    template: str


class TaskSpec(BaseModel):
    """Root schema for a physical-task YAML file."""
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    meta: MetaSpec
    substrate: SubstrateSpec
    transformation: TransformationSpec
    constructor: ConstructorSpec
    allowed_adjustments: AllowedAdjustments = Field(
        default_factory=AllowedAdjustments,
    )
    environment: EnvironmentSpec = Field(
        default_factory=EnvironmentSpec,
    )


# ── EvidencePacket (output JSON) ──────────────────────────────────────────

class GateResult(BaseModel):
    gate_name: str
    status: GateStatus
    measured_values: dict[str, Any] = Field(default_factory=dict)
    reason_code: str | None = None


class CounterfactualFix(BaseModel):
    type: FixType
    delta: float | None = None
    instruction: str
    proposed_patch: dict[str, Any] | None = None


class EvidencePacket(BaseModel):
    task_id: str
    verdict: Verdict
    failed_gate: str | None = None
    checks: list[GateResult] = Field(default_factory=list)
    counterfactual_fixes: list[CounterfactualFix] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    axiom_tfg_version: str = "0.1.0"
