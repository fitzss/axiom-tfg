"""Evidence-packet builder: runs all gates, assembles the packet, writes JSON."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from axiom_tfg.gates.ik_feasibility import check_ik_feasibility
from axiom_tfg.gates.keepout import check_keepout
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
            checks.append(result)
            if result.status == GateStatus.FAIL:
                failed_gate = result.gate_name
                all_fixes.extend(fixes)
                break  # fast red-light

    verdict = Verdict.HARD_CANT if failed_gate else Verdict.CAN

    return EvidencePacket(
        task_id=spec.task_id,
        verdict=verdict,
        failed_gate=failed_gate,
        checks=checks,
        counterfactual_fixes=all_fixes,
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
