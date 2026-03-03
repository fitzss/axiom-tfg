"""Evidence-packet builder: runs all gates, assembles the packet, writes JSON."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from axiom_tfg.gates.ik_feasibility import check_ik_feasibility
from axiom_tfg.gates.keepout import check_keepout
from axiom_tfg.gates.path_keepout import check_path_keepout
from axiom_tfg.gates.payload import check_payload
from axiom_tfg.gates.reachability import check_reachability
from axiom_tfg.models import (
    CounterfactualFix,
    EvidencePacket,
    GateResult,
    GateStatus,
    TaskSpec,
    Verdict,
)


def load_task_spec(path: Path) -> TaskSpec:
    """Read a YAML file and return a validated TaskSpec."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return TaskSpec.model_validate(raw)


def validate_task_spec(path: Path) -> list[str]:
    """Validate a YAML file against TaskSpec.  Returns a list of error strings
    (empty on success)."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        TaskSpec.model_validate(raw)
    except ValidationError as exc:
        return [str(e) for e in exc.errors()]
    return []


# ── Validation level mapping ──────────────────────────────────────────
# L0 = endpoint feasibility, L1 = path feasibility
GATE_LEVELS: dict[str, str] = {
    "ik_feasibility": "L0",
    "reachability": "L0",
    "payload": "L0",
    "keepout": "L0",
    "path_keepout": "L1",
}


def _tag_level(result: GateResult) -> None:
    """Set ``validation_level`` on a gate result from the mapping."""
    result.validation_level = GATE_LEVELS.get(result.gate_name)


def _compute_level_reached(
    checks: list[GateResult], failed_gate: str | None
) -> str | None:
    """Determine the highest validation level fully passed.

    Returns None if any L0 gate failed, "L0" if all L0 passed but L1
    failed or didn't run, "L1" if everything passed.
    """
    levels_seen: set[str] = set()
    for c in checks:
        if c.validation_level:
            levels_seen.add(c.validation_level)

    if failed_gate is not None:
        # Find the level of the failed gate.
        failed_level = GATE_LEVELS.get(failed_gate)
        if failed_level == "L0":
            return None  # L0 didn't fully pass
        if failed_level == "L1":
            return "L0"  # L0 passed, L1 failed

    # No failure — return the highest level that ran.
    if "L1" in levels_seen:
        return "L1"
    if "L0" in levels_seen:
        return "L0"
    return None


# Ordered pipeline of gates.  Each entry is a callable that returns
# ``(GateResult, fixes)`` or ``None`` when the gate should be skipped.
GATE_PIPELINE = [
    check_reachability,
    check_payload,
    check_keepout,
]


def run_gates(spec: TaskSpec) -> EvidencePacket:
    """Execute all gates in order, short-circuiting on the first failure.

    The IK feasibility gate is evaluated first when the constructor provides a
    ``urdf_path``.  If IK runs and passes, the simpler spherical reachability
    gate is skipped (IK subsumes it).  If no URDF is provided, IK is skipped
    and the spherical gate runs as the fallback.
    """
    checks: list[GateResult] = []
    all_fixes: list[CounterfactualFix] = []
    failed_gate: str | None = None

    # ── IK gate (optional, runs before the standard pipeline) ─────────
    skip_spherical_reach = False
    ik_result = check_ik_feasibility(spec)
    if ik_result is not None:
        result, fixes = ik_result
        _tag_level(result)
        checks.append(result)
        if result.status == GateStatus.FAIL:
            failed_gate = result.gate_name
            all_fixes.extend(fixes)
        else:
            # IK passed — spherical reachability is redundant.
            skip_spherical_reach = True

    # ── Standard gate pipeline ────────────────────────────────────────
    if failed_gate is None:
        for gate_fn in GATE_PIPELINE:
            if skip_spherical_reach and gate_fn is check_reachability:
                continue
            result, fixes = gate_fn(spec)
            _tag_level(result)
            checks.append(result)
            if result.status == GateStatus.FAIL:
                failed_gate = result.gate_name
                all_fixes.extend(fixes)
                break  # fast red-light

    # ── Path keepout gate (optional, runs after endpoint keepout) ──
    if failed_gate is None:
        path_result = check_path_keepout(spec)
        if path_result is not None:
            result, fixes = path_result
            _tag_level(result)
            checks.append(result)
            if result.status == GateStatus.FAIL:
                failed_gate = result.gate_name
                all_fixes.extend(fixes)

    verdict = Verdict.HARD_CANT if failed_gate else Verdict.CAN

    return EvidencePacket(
        task_id=spec.task_id,
        verdict=verdict,
        failed_gate=failed_gate,
        checks=checks,
        counterfactual_fixes=all_fixes,
        validation_level_reached=_compute_level_reached(checks, failed_gate),
    )


def write_evidence(packet: EvidencePacket, out_dir: Path) -> Path:
    """Serialise the packet to ``<out_dir>/<task_id>/evidence.json``."""
    dest = out_dir / packet.task_id
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "evidence.json"
    path.write_text(
        json.dumps(packet.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return path
