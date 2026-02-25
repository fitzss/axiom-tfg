"""Tests for the ``axiom init`` command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from axiom_cli.app import app

runner = CliRunner()


class TestAxiomInit:
    """axiom init scaffolding."""

    def test_creates_expected_files(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0

        # Profiles
        assert (tmp_path / "axiom_profiles" / "robot_ur5e.yaml").exists()
        assert (tmp_path / "axiom_profiles" / "environment_default.yaml").exists()

        # Tasks
        assert (tmp_path / "tasks" / "pick_place_can.yaml").exists()
        assert (tmp_path / "tasks" / "pick_place_cant_payload.yaml").exists()
        assert (tmp_path / "tasks" / "pick_place_cant_keepout.yaml").exists()

        # Regressions (artifact bundles)
        for stem in ("pick_place_can", "pick_place_cant_payload", "pick_place_cant_keepout"):
            bundle = tmp_path / "regressions" / stem
            assert (bundle / "input.yaml").exists()
            assert (bundle / "result.json").exists()
            assert (bundle / "evidence.json").exists()

        # CI
        assert (tmp_path / ".github" / "workflows" / "axiom.yml").exists()

        # Makefile
        assert (tmp_path / "Makefile").exists()

    def test_idempotent_skips_existing(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])

        # Write a sentinel into Makefile.
        makefile = tmp_path / "Makefile"
        makefile.write_text("# custom\n")

        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        assert "skipped" in result.output

        # Makefile should NOT be overwritten.
        assert makefile.read_text() == "# custom\n"

    def test_force_overwrites(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])

        makefile = tmp_path / "Makefile"
        makefile.write_text("# custom\n")

        result = runner.invoke(app, ["init", str(tmp_path), "--force"])
        assert result.exit_code == 0

        # Makefile SHOULD be overwritten.
        assert makefile.read_text() != "# custom\n"
        assert "axiom-demo" in makefile.read_text()

    def test_regression_bundles_are_replayable(self, tmp_path: Path) -> None:
        """Regressions created by init should pass replay."""
        runner.invoke(app, ["init", str(tmp_path)])

        replay_out = tmp_path / "replay_out"
        result = runner.invoke(
            app, ["replay", str(tmp_path / "regressions"), "--out", str(replay_out)]
        )
        assert result.exit_code == 0
        assert "failed=0" in result.output

    def test_output_includes_next_steps(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert "Next steps" in result.output
        assert "axiom run" in result.output

    def test_can_verdict_from_init_task(self, tmp_path: Path) -> None:
        """The CAN task produced by init should pass gates."""
        runner.invoke(app, ["init", str(tmp_path)])
        out = tmp_path / "run_out"
        result = runner.invoke(
            app, ["run", str(tmp_path / "tasks" / "pick_place_can.yaml"), "--out", str(out)]
        )
        assert result.exit_code == 0
        assert "VERDICT=CAN" in result.output
