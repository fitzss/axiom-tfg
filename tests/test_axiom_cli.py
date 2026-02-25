"""Tests for the axiom CLI (axiom_cli.app) — run, sweep, replay commands."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from typer.testing import CliRunner

from axiom_cli.app import app

runner = CliRunner()

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


# ── axiom run ────────────────────────────────────────────────────────────


class TestRun:
    """axiom run command."""

    def test_run_can_produces_artifacts(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        result = runner.invoke(
            app, ["run", str(EXAMPLES / "pick_place_can.yaml"), "--out", str(out), "--junit"]
        )
        assert result.exit_code == 0
        assert "VERDICT=CAN" in result.output

        # Artifact bundle contents.
        assert (out / "input.yaml").exists()
        assert (out / "result.json").exists()
        assert (out / "evidence.json").exists()
        assert (out / "junit.xml").exists()

        res = json.loads((out / "result.json").read_text())
        assert res["verdict"] == "CAN"
        assert res["failed_gate"] is None

    def test_run_hard_cant_exit_code_2(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        result = runner.invoke(
            app, ["run", str(EXAMPLES / "pick_place_cant_reach.yaml"), "--out", str(out)]
        )
        assert result.exit_code == 2
        assert "VERDICT=HARD_CANT" in result.output
        assert "gate=reachability" in result.output

    def test_run_cant_payload_exit_code_2(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        result = runner.invoke(
            app, ["run", str(EXAMPLES / "pick_place_cant_payload.yaml"), "--out", str(out)]
        )
        assert result.exit_code == 2
        assert "VERDICT=HARD_CANT" in result.output

    def test_run_evidence_json_matches_model(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        runner.invoke(
            app, ["run", str(EXAMPLES / "pick_place_can.yaml"), "--out", str(out)]
        )
        ev = json.loads((out / "evidence.json").read_text())
        assert ev["verdict"] == "CAN"
        assert len(ev["checks"]) >= 3
        assert "created_at" in ev

    def test_run_invalid_yaml_exit_code_1(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("not_a_taskspec: true\n")
        result = runner.invoke(app, ["run", str(bad), "--out", str(tmp_path / "out")])
        assert result.exit_code == 1

    def test_run_console_line_format(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        result = runner.invoke(
            app, ["run", str(EXAMPLES / "pick_place_cant_reach.yaml"), "--out", str(out)]
        )
        # Should contain all required fields.
        assert "VERDICT=" in result.output
        assert "gate=" in result.output
        assert "reason=" in result.output
        assert "out=" in result.output

    def test_junit_xml_failure_on_hard_cant(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        runner.invoke(
            app,
            ["run", str(EXAMPLES / "pick_place_cant_reach.yaml"), "--out", str(out), "--junit"],
        )
        tree = ET.parse(out / "junit.xml")
        root = tree.getroot()
        assert root.tag == "testsuite"
        assert root.get("failures") == "1"
        tc = root.find("testcase")
        assert tc is not None
        failure = tc.find("failure")
        assert failure is not None
        assert "HARD_CANT" in (failure.text or "")

    def test_junit_xml_pass_on_can(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        runner.invoke(
            app,
            ["run", str(EXAMPLES / "pick_place_can.yaml"), "--out", str(out), "--junit"],
        )
        tree = ET.parse(out / "junit.xml")
        root = tree.getroot()
        assert root.get("failures") == "0"
        tc = root.find("testcase")
        assert tc is not None
        assert tc.find("failure") is None

    def test_run_no_junit_by_default(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        runner.invoke(
            app, ["run", str(EXAMPLES / "pick_place_can.yaml"), "--out", str(out)]
        )
        assert not (out / "junit.xml").exists()


# ── axiom sweep ──────────────────────────────────────────────────────────


class TestSweep:
    """axiom sweep command."""

    def test_sweep_writes_csv_with_n_rows(self, tmp_path: Path) -> None:
        out = tmp_path / "sweep_out"
        result = runner.invoke(
            app,
            [
                "sweep",
                str(EXAMPLES / "pick_place_can.yaml"),
                "--n", "10",
                "--seed", "42",
                "--mass-min", "0.1",
                "--mass-max", "6.0",
                "--out", str(out),
            ],
        )
        # Exit code may be 0 or 2 depending on sampled masses.
        assert result.exit_code in (0, 2)

        csv_path = out / "sweep.csv"
        assert csv_path.exists()
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 10

        # Required columns.
        for col in ("run_id", "verdict", "failed_gate", "reason_code", "mass_kg"):
            assert col in rows[0]

    def test_sweep_writes_sweep_json(self, tmp_path: Path) -> None:
        out = tmp_path / "sweep_out"
        runner.invoke(
            app,
            [
                "sweep",
                str(EXAMPLES / "pick_place_can.yaml"),
                "--n", "5",
                "--seed", "99",
                "--mass-min", "0.1",
                "--mass-max", "2.0",
                "--out", str(out),
            ],
        )
        data = json.loads((out / "sweep.json").read_text())
        assert data["n"] == 5
        assert data["seed"] == 99
        assert "summary" in data
        assert data["summary"]["CAN"] + data["summary"]["HARD_CANT"] == 5

    def test_sweep_writes_junit(self, tmp_path: Path) -> None:
        out = tmp_path / "sweep_out"
        runner.invoke(
            app,
            [
                "sweep",
                str(EXAMPLES / "pick_place_can.yaml"),
                "--n", "5",
                "--seed", "99",
                "--mass-min", "0.1",
                "--mass-max", "2.0",
                "--out", str(out),
            ],
        )
        tree = ET.parse(out / "junit.xml")
        root = tree.getroot()
        assert root.tag == "testsuite"
        assert root.get("tests") == "5"

    def test_sweep_deterministic(self, tmp_path: Path) -> None:
        """Same seed produces same results."""
        args = [
            "sweep",
            str(EXAMPLES / "pick_place_can.yaml"),
            "--n", "10",
            "--seed", "42",
            "--mass-min", "0.1",
            "--mass-max", "6.0",
        ]
        out1 = tmp_path / "s1"
        out2 = tmp_path / "s2"
        runner.invoke(app, [*args, "--out", str(out1)])
        runner.invoke(app, [*args, "--out", str(out2)])

        with open(out1 / "sweep.csv") as f1, open(out2 / "sweep.csv") as f2:
            assert f1.read() == f2.read()

    def test_sweep_exit_0_when_all_can(self, tmp_path: Path) -> None:
        """Small mass range that should all pass payload gate."""
        out = tmp_path / "sweep_out"
        result = runner.invoke(
            app,
            [
                "sweep",
                str(EXAMPLES / "pick_place_can.yaml"),
                "--n", "5",
                "--seed", "1",
                "--mass-min", "0.1",
                "--mass-max", "0.5",
                "--out", str(out),
            ],
        )
        assert result.exit_code == 0

    def test_sweep_exit_2_when_any_cant(self, tmp_path: Path) -> None:
        """Large mass range that should trigger payload failures."""
        out = tmp_path / "sweep_out"
        result = runner.invoke(
            app,
            [
                "sweep",
                str(EXAMPLES / "pick_place_can.yaml"),
                "--n", "20",
                "--seed", "1",
                "--mass-min", "0.1",
                "--mass-max", "100.0",
                "--out", str(out),
            ],
        )
        assert result.exit_code == 2

    def test_sweep_no_junit_flag(self, tmp_path: Path) -> None:
        out = tmp_path / "sweep_out"
        runner.invoke(
            app,
            [
                "sweep",
                str(EXAMPLES / "pick_place_can.yaml"),
                "--n", "3",
                "--seed", "1",
                "--mass-min", "0.1",
                "--mass-max", "0.5",
                "--no-junit",
                "--out", str(out),
            ],
        )
        assert not (out / "junit.xml").exists()


# ── axiom replay ─────────────────────────────────────────────────────────


class TestReplay:
    """axiom replay command."""

    def _create_bundle(self, base_dir: Path, name: str, yaml_path: Path) -> Path:
        """Run a task and write an artifact bundle for later replay."""
        bundle = base_dir / name
        runner.invoke(
            app, ["run", str(yaml_path), "--out", str(bundle)]
        )
        return bundle

    def test_replay_all_match_exit_0(self, tmp_path: Path) -> None:
        bundles = tmp_path / "bundles"
        self._create_bundle(bundles, "can", EXAMPLES / "pick_place_can.yaml")
        self._create_bundle(bundles, "reach", EXAMPLES / "pick_place_cant_reach.yaml")

        replay_out = tmp_path / "replay_out"
        result = runner.invoke(app, ["replay", str(bundles), "--out", str(replay_out)])
        assert result.exit_code == 0
        assert "passed=2" in result.output
        assert "failed=0" in result.output

        report = json.loads((replay_out / "replay_report.json").read_text())
        assert report["passed"] == 2
        assert report["failed"] == 0

    def test_replay_mismatch_exit_2(self, tmp_path: Path) -> None:
        """Tamper with saved result.json to simulate a regression."""
        bundles = tmp_path / "bundles"
        self._create_bundle(bundles, "can", EXAMPLES / "pick_place_can.yaml")

        # Tamper: change saved verdict from CAN to HARD_CANT.
        result_json = bundles / "can" / "result.json"
        saved = json.loads(result_json.read_text())
        saved["verdict"] = "HARD_CANT"
        saved["failed_gate"] = "payload"
        saved["reason_code"] = "OVER_PAYLOAD"
        result_json.write_text(json.dumps(saved, indent=2) + "\n")

        replay_out = tmp_path / "replay_out"
        result = runner.invoke(app, ["replay", str(bundles), "--out", str(replay_out)])
        assert result.exit_code == 2
        assert "failed=1" in result.output

    def test_replay_junit_has_failure_on_mismatch(self, tmp_path: Path) -> None:
        bundles = tmp_path / "bundles"
        self._create_bundle(bundles, "can", EXAMPLES / "pick_place_can.yaml")

        # Tamper.
        result_json = bundles / "can" / "result.json"
        saved = json.loads(result_json.read_text())
        saved["verdict"] = "HARD_CANT"
        result_json.write_text(json.dumps(saved))

        replay_out = tmp_path / "replay_out"
        runner.invoke(app, ["replay", str(bundles), "--out", str(replay_out)])

        tree = ET.parse(replay_out / "junit.xml")
        root = tree.getroot()
        assert int(root.get("failures", "0")) >= 1

        tc = root.find("testcase")
        assert tc is not None
        failure = tc.find("failure")
        assert failure is not None
        assert "expected" in (failure.text or "").lower()

    def test_replay_manifest_file(self, tmp_path: Path) -> None:
        """Replay from a manifest text file listing bundle paths."""
        bundles = tmp_path / "bundles"
        b1 = self._create_bundle(bundles, "can", EXAMPLES / "pick_place_can.yaml")

        manifest = tmp_path / "manifest.txt"
        manifest.write_text(f"{b1}\n")

        replay_out = tmp_path / "replay_out"
        result = runner.invoke(app, ["replay", str(manifest), "--out", str(replay_out)])
        assert result.exit_code == 0
        assert "passed=1" in result.output

    def test_replay_junit_flag_exit_0(self, tmp_path: Path) -> None:
        """--junit --out produces replay_report.json and junit.xml, exits 0 when no diffs."""
        bundles = tmp_path / "bundles"
        self._create_bundle(bundles, "can", EXAMPLES / "pick_place_can.yaml")

        replay_out = tmp_path / "replay_out"
        result = runner.invoke(
            app, ["replay", str(bundles), "--junit", "--out", str(replay_out)]
        )
        assert result.exit_code == 0
        assert (replay_out / "replay_report.json").exists()
        assert (replay_out / "junit.xml").exists()

    def test_replay_no_junit_flag(self, tmp_path: Path) -> None:
        """--no-junit suppresses junit.xml output."""
        bundles = tmp_path / "bundles"
        self._create_bundle(bundles, "can", EXAMPLES / "pick_place_can.yaml")

        replay_out = tmp_path / "replay_out"
        runner.invoke(
            app, ["replay", str(bundles), "--no-junit", "--out", str(replay_out)]
        )
        assert (replay_out / "replay_report.json").exists()
        assert not (replay_out / "junit.xml").exists()

    def test_replay_report_structure(self, tmp_path: Path) -> None:
        bundles = tmp_path / "bundles"
        self._create_bundle(bundles, "can", EXAMPLES / "pick_place_can.yaml")

        replay_out = tmp_path / "replay_out"
        runner.invoke(app, ["replay", str(bundles), "--out", str(replay_out)])

        report = json.loads((replay_out / "replay_report.json").read_text())
        assert "total" in report
        assert "passed" in report
        assert "failed" in report
        assert "diffs" in report
        assert len(report["diffs"]) == 1

        diff = report["diffs"][0]
        assert "artifact" in diff
        assert "status" in diff
        assert "expected_verdict" in diff
        assert "actual_verdict" in diff
