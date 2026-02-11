"""Scenario Sweep — generate TaskSpec variants and run the gate pipeline."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from axiom_tfg.models import TaskSpec


@dataclass
class RangeSpec:
    min: float
    max: float


@dataclass
class XYZRangeSpec:
    x: RangeSpec | None = None
    y: RangeSpec | None = None
    z: RangeSpec | None = None


@dataclass
class VariationSpec:
    mass_kg: RangeSpec | None = None
    target_xyz: XYZRangeSpec | None = None


@dataclass
class SweepRequest:
    base_task: TaskSpec
    variations: VariationSpec = field(default_factory=VariationSpec)
    n: int = 50
    seed: int = 1337


def _parse_range(d: dict | None) -> RangeSpec | None:
    if not d or not isinstance(d, dict):
        return None
    mn, mx = d.get("min"), d.get("max")
    if mn is None or mx is None:
        return None
    return RangeSpec(min=float(mn), max=float(mx))


def _parse_xyz_range(d: dict | None) -> XYZRangeSpec | None:
    if not d or not isinstance(d, dict):
        return None
    spec = XYZRangeSpec(
        x=_parse_range(d.get("x")),
        y=_parse_range(d.get("y")),
        z=_parse_range(d.get("z")),
    )
    if spec.x is None and spec.y is None and spec.z is None:
        return None
    return spec


def parse_variations(raw: dict | None) -> VariationSpec:
    """Parse a raw JSON dict into a VariationSpec."""
    if not raw or not isinstance(raw, dict):
        return VariationSpec()
    return VariationSpec(
        mass_kg=_parse_range(raw.get("mass_kg")),
        target_xyz=_parse_xyz_range(raw.get("target_xyz")),
    )


def generate_variants(base: TaskSpec, req: SweepRequest) -> list[TaskSpec]:
    """Generate *n* TaskSpec variants by sampling within provided ranges.

    Uses ``random.Random(seed)`` for full determinism — no numpy needed.
    """
    rng = random.Random(req.seed)
    variants: list[TaskSpec] = []

    for i in range(req.n):
        data = base.model_dump()
        data["task_id"] = f"{base.task_id}-sweep-{i:04d}"

        v = req.variations

        if v.mass_kg is not None:
            data["substrate"]["mass_kg"] = rng.uniform(v.mass_kg.min, v.mass_kg.max)

        if v.target_xyz is not None:
            xyz = list(data["transformation"]["target_pose"]["xyz"])
            if v.target_xyz.x is not None:
                xyz[0] = rng.uniform(v.target_xyz.x.min, v.target_xyz.x.max)
            if v.target_xyz.y is not None:
                xyz[1] = rng.uniform(v.target_xyz.y.min, v.target_xyz.y.max)
            if v.target_xyz.z is not None:
                xyz[2] = rng.uniform(v.target_xyz.z.min, v.target_xyz.z.max)
            data["transformation"]["target_pose"]["xyz"] = xyz

        variants.append(TaskSpec.model_validate(data))

    return variants


def build_summary(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a summary from a list of run result dicts (verdict, failed_gate, etc.)."""
    can_count = 0
    cant_count = 0
    by_gate: dict[str, int] = {}
    by_reason: dict[str, int] = {}

    for r in run_results:
        if r["verdict"] == "CAN":
            can_count += 1
        else:
            cant_count += 1
        gate = r.get("failed_gate")
        if gate:
            by_gate[gate] = by_gate.get(gate, 0) + 1

        # Extract reason codes from evidence checks.
        evidence = r.get("evidence")
        if evidence:
            for check in evidence.get("checks", []):
                rc = check.get("reason_code")
                if rc:
                    by_reason[rc] = by_reason.get(rc, 0) + 1

    top_reasons = sorted(by_reason.items(), key=lambda x: -x[1])

    return {
        "CAN": can_count,
        "HARD_CANT": cant_count,
        "by_failed_gate": by_gate,
        "top_reasons": [{"reason_code": rc, "count": c} for rc, c in top_reasons],
    }
