"""Tests for the ROS2 taskspec_mapping module (pure Python, no ROS deps)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the ROS2 package importable without colcon/install.
_ROS2_PKG = Path(__file__).resolve().parent.parent / "ros2" / "axiom_preflight_nav2"
if str(_ROS2_PKG) not in sys.path:
    sys.path.insert(0, str(_ROS2_PKG))

from axiom_preflight_nav2.taskspec_mapping import goal_to_taskspec, load_keepout_zones  # noqa: E402

from axiom_tfg.models import TaskSpec  # noqa: E402


class TestGoalToTaskspec:
    """goal_to_taskspec produces a valid, deterministic TaskSpec dict."""

    def test_basic_structure(self) -> None:
        spec = goal_to_taskspec(goal_x=3.0, goal_y=4.0, goal_uuid="abc123")
        assert spec["task_id"] == "nav2-abc123"
        assert spec["meta"]["template"] == "navigate_to_pose"
        assert spec["transformation"]["target_pose"]["xyz"] == [3.0, 4.0, 0.0]
        assert spec["constructor"]["id"] == "diffbot"
        assert spec["constructor"]["max_reach_m"] == 10.0
        assert spec["constructor"]["max_payload_kg"] == 9999.0
        assert spec["substrate"]["id"] == "robot"

    def test_custom_params(self) -> None:
        spec = goal_to_taskspec(
            goal_x=1.0,
            goal_y=2.0,
            goal_uuid="xyz",
            robot_model="turtlebot4",
            max_nav_radius_m=5.0,
            safety_buffer_m=0.5,
        )
        assert spec["constructor"]["id"] == "turtlebot4"
        assert spec["constructor"]["max_reach_m"] == 5.0
        assert spec["environment"]["safety_buffer"] == 0.5

    def test_validates_as_taskspec(self) -> None:
        """The output dict must pass Pydantic validation."""
        d = goal_to_taskspec(goal_x=1.0, goal_y=2.0, goal_uuid="val1")
        spec = TaskSpec.model_validate(d)
        assert spec.task_id == "nav2-val1"
        assert spec.constructor.max_reach_m == 10.0

    def test_determinism(self) -> None:
        """Same inputs produce identical dicts."""
        kwargs = dict(goal_x=5.0, goal_y=-3.0, goal_uuid="det1", robot_model="spot")
        a = goal_to_taskspec(**kwargs)
        b = goal_to_taskspec(**kwargs)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_keepout_zones_included(self) -> None:
        zones = [
            {"id": "wall", "min_xyz": [1.0, 1.0, 0.0], "max_xyz": [2.0, 2.0, 1.0]}
        ]
        spec = goal_to_taskspec(
            goal_x=0.0, goal_y=0.0, goal_uuid="kz1", keepout_zones=zones
        )
        assert spec["environment"]["keepout_zones"] == zones

    def test_no_keepout_zones_by_default(self) -> None:
        spec = goal_to_taskspec(goal_x=0.0, goal_y=0.0, goal_uuid="nkz")
        assert "keepout_zones" not in spec["environment"]

    def test_can_verdict_within_radius(self) -> None:
        """A goal within max_nav_radius_m should get CAN from the gates."""
        from axiom_tfg.runner import run_taskspec

        d = goal_to_taskspec(goal_x=1.0, goal_y=1.0, goal_uuid="can1")
        spec = TaskSpec.model_validate(d)
        result, _packet = run_taskspec(spec)
        assert result["verdict"] == "CAN"

    def test_hard_cant_beyond_radius(self) -> None:
        """A goal beyond max_nav_radius_m should get HARD_CANT."""
        from axiom_tfg.runner import run_taskspec

        d = goal_to_taskspec(
            goal_x=100.0, goal_y=0.0, goal_uuid="cant1", max_nav_radius_m=5.0
        )
        spec = TaskSpec.model_validate(d)
        result, _packet = run_taskspec(spec)
        assert result["verdict"] == "HARD_CANT"
        assert result["failed_gate"] == "reachability"

    def test_hard_cant_in_keepout(self) -> None:
        """A goal inside a keepout zone should get HARD_CANT."""
        from axiom_tfg.runner import run_taskspec

        zones = [
            {"id": "blocked", "min_xyz": [0.5, 0.5, -1.0], "max_xyz": [1.5, 1.5, 1.0]}
        ]
        d = goal_to_taskspec(
            goal_x=1.0, goal_y=1.0, goal_uuid="kz_cant", keepout_zones=zones
        )
        spec = TaskSpec.model_validate(d)
        result, _packet = run_taskspec(spec)
        assert result["verdict"] == "HARD_CANT"
        assert result["failed_gate"] == "keepout"


class TestLoadKeepoutZones:
    """load_keepout_zones reads zone YAML files."""

    def test_loads_valid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "zones.yaml"
        f.write_text(
            "keepout_zones:\n"
            "  - id: table\n"
            "    min_xyz: [0, 0, 0]\n"
            "    max_xyz: [1, 1, 1]\n"
        )
        zones = load_keepout_zones(f)
        assert len(zones) == 1
        assert zones[0]["id"] == "table"

    def test_returns_empty_for_none(self) -> None:
        assert load_keepout_zones(None) == []

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        assert load_keepout_zones(tmp_path / "nope.yaml") == []

    def test_returns_empty_for_empty_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yaml"
        f.write_text("{}\n")
        assert load_keepout_zones(f) == []

    def test_multiple_zones(self, tmp_path: Path) -> None:
        f = tmp_path / "zones.yaml"
        f.write_text(
            "keepout_zones:\n"
            "  - id: a\n"
            "    min_xyz: [0, 0, 0]\n"
            "    max_xyz: [1, 1, 1]\n"
            "  - id: b\n"
            "    min_xyz: [2, 2, 0]\n"
            "    max_xyz: [3, 3, 1]\n"
        )
        zones = load_keepout_zones(f)
        assert len(zones) == 2
        assert zones[1]["id"] == "b"
