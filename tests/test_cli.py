"""Integration tests for the CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from axiom_tfg.cli import app

runner = CliRunner()

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_run_can(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(EXAMPLES / "pick_place_can.yaml"), "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert "CAN" in result.output

    evidence = tmp_path / "pick-place-001" / "evidence.json"
    assert evidence.exists()
    data = json.loads(evidence.read_text())
    assert data["verdict"] == "CAN"
    assert data["failed_gate"] is None
    assert len(data["checks"]) == 3  # all gates ran


def test_run_cant_reach(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(EXAMPLES / "pick_place_cant_reach.yaml"), "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "HARD_CANT" in result.output
    assert "OUT_OF_REACH" in result.output

    evidence = tmp_path / "pick-place-002-reach" / "evidence.json"
    data = json.loads(evidence.read_text())
    assert data["verdict"] == "HARD_CANT"
    assert data["failed_gate"] == "reachability"
    # Only one gate ran (short-circuit)
    assert len(data["checks"]) == 1
    assert len(data["counterfactual_fixes"]) >= 1


def test_run_cant_payload(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(EXAMPLES / "pick_place_cant_payload.yaml"), "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "HARD_CANT" in result.output
    assert "OVER_PAYLOAD" in result.output

    evidence = tmp_path / "pick-place-003-payload" / "evidence.json"
    data = json.loads(evidence.read_text())
    assert data["verdict"] == "HARD_CANT"
    assert data["failed_gate"] == "payload"
    splits = [f for f in data["counterfactual_fixes"] if f["type"] == "SPLIT_PAYLOAD"]
    assert len(splits) == 1
    assert splits[0]["proposed_patch"]["suggested_payload_split_count"] == 5


def test_validate_ok() -> None:
    result = runner.invoke(app, ["validate", str(EXAMPLES / "pick_place_can.yaml")])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_validate_bad_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("meta: 123\n")
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1
    assert "Validation errors" in result.output


def test_evidence_has_timestamps(tmp_path: Path) -> None:
    runner.invoke(app, ["run", str(EXAMPLES / "pick_place_can.yaml"), "--out", str(tmp_path)])
    evidence = tmp_path / "pick-place-001" / "evidence.json"
    data = json.loads(evidence.read_text())
    assert "created_at" in data
    assert "axiom_tfg_version" in data


# ── demo command ───────────────────────────────────────────────────────────


def test_demo_exit_code(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "--out", str(tmp_path)])
    assert result.exit_code == 0


def test_demo_writes_all_evidence(tmp_path: Path) -> None:
    runner.invoke(app, ["demo", "--out", str(tmp_path)])
    assert (tmp_path / "pick-place-001" / "evidence.json").exists()
    assert (tmp_path / "pick-place-002-reach" / "evidence.json").exists()
    assert (tmp_path / "pick-place-003-payload" / "evidence.json").exists()
    assert (tmp_path / "pick-place-004-keepout" / "evidence.json").exists()


def test_demo_output_contains_verdicts(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "--out", str(tmp_path)])
    assert "CAN" in result.output
    assert "HARD_CANT" in result.output
    # All example filenames appear in the table
    assert "pick_place_can.yaml" in result.output
    assert "pick_place_cant_reach.yaml" in result.output
    assert "pick_place_cant_payload.yaml" in result.output
    assert "pick_place_cant_keepout.yaml" in result.output
