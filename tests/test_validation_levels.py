"""Tests for validation level tagging on gate results and evidence packets."""

from __future__ import annotations

from axiom_tfg.sdk import check_simple


class TestValidationLevels:
    """validation_level on GateResult and validation_level_reached on Result."""

    def test_all_pass_reaches_l0(self) -> None:
        """Reachable, light target with no keepout → validation_level_reached = L0."""
        r = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=0.35)
        assert r.verdict == "CAN"
        assert r.validation_level_reached == "L0"

    def test_fail_at_reach_returns_none(self) -> None:
        """Unreachable target → L0 failed → validation_level_reached = None."""
        r = check_simple(target_xyz=[5.0, 5.0, 5.0], mass_kg=0.35)
        assert r.verdict == "HARD_CANT"
        assert r.validation_level_reached is None

    def test_fail_at_payload_returns_none(self) -> None:
        """Over-payload → L0 failed → validation_level_reached = None."""
        r = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=100.0)
        assert r.verdict == "HARD_CANT"
        assert r.validation_level_reached is None

    def test_fail_at_keepout_returns_none(self) -> None:
        """Target in keepout zone → L0 failed → validation_level_reached = None."""
        r = check_simple(
            target_xyz=[0.5, 0.5, 0.5],
            mass_kg=0.35,
            keepout_zones=[
                {"id": "box", "min_xyz": [0.3, 0.3, 0.0], "max_xyz": [0.7, 0.7, 1.0]},
            ],
        )
        assert r.verdict == "HARD_CANT"
        assert r.validation_level_reached is None

    def test_gate_results_have_levels(self) -> None:
        """Each gate result in the evidence has a validation_level set."""
        r = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=0.35)
        checks = r.evidence.get("checks", [])
        assert len(checks) > 0
        for c in checks:
            assert c["validation_level"] is not None, (
                f"Gate {c['gate_name']} missing validation_level"
            )

    def test_result_to_dict_includes_level(self) -> None:
        """to_dict() includes validation_level_reached."""
        r = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=0.35)
        d = r.to_dict()
        assert "validation_level_reached" in d
        assert d["validation_level_reached"] == "L0"
