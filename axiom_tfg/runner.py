"""Shared pipeline runner — single entry-point used by both CLI and API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from axiom_tfg.evidence import run_gates
from axiom_tfg.models import EvidencePacket, TaskSpec


def run_taskspec(spec: TaskSpec) -> tuple[dict[str, Any], EvidencePacket]:
    """Run the gate pipeline on a validated TaskSpec.

    Returns (result_dict, evidence_packet) where *result_dict* contains the
    flat summary fields used by sweep summaries and artifact bundles.
    """
    packet = run_gates(spec)

    top_fix: str | None = None
    if packet.counterfactual_fixes:
        top_fix = packet.counterfactual_fixes[0].type.value

    reason_code: str | None = None
    for check in packet.checks:
        if check.reason_code:
            reason_code = check.reason_code
            break

    result = {
        "verdict": packet.verdict.value,
        "failed_gate": packet.failed_gate,
        "reason_code": reason_code,
        "top_fix": top_fix,
        "evidence": packet.model_dump(mode="json"),
    }
    return result, packet


def load_and_run(path: Path) -> tuple[dict[str, Any], EvidencePacket, TaskSpec]:
    """Load a YAML/JSON task file and run the pipeline.

    Returns (result_dict, evidence_packet, spec).
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    spec = TaskSpec.model_validate(raw)
    result, packet = run_taskspec(spec)
    return result, packet, spec


def write_artifact_bundle(
    spec: TaskSpec,
    packet: EvidencePacket,
    result: dict[str, Any],
    out_dir: Path,
    *,
    junit: bool = False,
) -> Path:
    """Write a complete artifact bundle to *out_dir*.

    Contents:
      input.yaml      — normalised TaskSpec
      result.json     — verdict summary
      evidence.json   — full evidence packet
      junit.xml       — (optional) JUnit XML with one testcase
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # input.yaml
    (out_dir / "input.yaml").write_text(
        yaml.dump(spec.model_dump(mode="json"), default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # result.json (without the nested evidence blob)
    result_slim = {k: v for k, v in result.items() if k != "evidence"}
    (out_dir / "result.json").write_text(
        json.dumps(result_slim, indent=2) + "\n",
        encoding="utf-8",
    )

    # evidence.json
    (out_dir / "evidence.json").write_text(
        json.dumps(packet.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    if junit:
        xml = _junit_single(spec.task_id, packet)
        (out_dir / "junit.xml").write_text(xml, encoding="utf-8")

    return out_dir


# ── JUnit XML helpers ─────────────────────────────────────────────────────


def _esc(text: str) -> str:
    """Escape XML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _junit_single(task_id: str, packet: EvidencePacket) -> str:
    """Build JUnit XML with one testcase for a single run."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    suite = Element("testsuite", name="axiom", tests="1")
    tc = SubElement(suite, "testcase", name=task_id, classname="axiom.run")
    if packet.verdict.value == "HARD_CANT":
        failure = SubElement(tc, "failure", message=f"gate={packet.failed_gate}")
        reason = ""
        for c in packet.checks:
            if c.reason_code:
                reason = c.reason_code
                break
        failure.text = f"verdict=HARD_CANT gate={packet.failed_gate} reason={reason}"
        suite.set("failures", "1")
    else:
        suite.set("failures", "0")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(suite, encoding="unicode")


def junit_from_runs(results: list[dict[str, Any]]) -> str:
    """Build JUnit XML where each run is a testcase (used by sweep/replay)."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    n = len(results)
    failures = sum(1 for r in results if r.get("verdict") != "CAN")

    suite = Element("testsuite", name="axiom", tests=str(n), failures=str(failures))
    for r in results:
        task_id = r.get("task_id", r.get("run_id", "unknown"))
        tc = SubElement(suite, "testcase", name=task_id, classname="axiom.run")
        if r.get("verdict") != "CAN":
            msg = f"gate={r.get('failed_gate')}"
            failure = SubElement(tc, "failure", message=msg)
            failure.text = (
                f"verdict={r.get('verdict')} "
                f"gate={r.get('failed_gate')} "
                f"reason={r.get('reason_code')}"
            )
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(suite, encoding="unicode")


def junit_from_replay(diffs: list[dict[str, Any]]) -> str:
    """Build JUnit XML for replay comparisons."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    n = len(diffs)
    failures = sum(1 for d in diffs if d.get("status") == "FAIL")

    suite = Element("testsuite", name="axiom.replay", tests=str(n), failures=str(failures))
    for d in diffs:
        name = d.get("artifact", "unknown")
        tc = SubElement(suite, "testcase", name=name, classname="axiom.replay")
        if d.get("status") == "FAIL":
            msg = "verdict/gate/reason mismatch"
            failure = SubElement(tc, "failure", message=msg)
            failure.text = (
                f"expected: verdict={d.get('expected_verdict')} "
                f"gate={d.get('expected_gate')} "
                f"reason={d.get('expected_reason')}\n"
                f"actual:   verdict={d.get('actual_verdict')} "
                f"gate={d.get('actual_gate')} "
                f"reason={d.get('actual_reason')}"
            )
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(suite, encoding="unicode")
