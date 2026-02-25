"""Pure-Python mapping from a NavigateToPose goal to an Axiom TaskSpec dict.

No ROS dependencies — this module only uses stdlib + PyYAML so it can be
tested without a ROS workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_keepout_zones(path: str | Path | None) -> list[dict[str, Any]]:
    """Load keepout zones from a YAML file.

    Expected format::

        keepout_zones:
          - id: obstacle_1
            min_xyz: [1.0, 2.0, 0.0]
            max_xyz: [3.0, 4.0, 1.0]

    Returns an empty list when *path* is ``None`` or the file has no zones.
    """
    if path is None:
        return []
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    return raw.get("keepout_zones") or []


def goal_to_taskspec(
    *,
    goal_x: float,
    goal_y: float,
    goal_uuid: str,
    robot_model: str = "diffbot",
    max_nav_radius_m: float = 10.0,
    safety_buffer_m: float = 0.2,
    keepout_zones: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a TaskSpec dict from NavigateToPose goal fields.

    This is a deterministic, pure function: same inputs always produce the
    same dict (suitable for artifact replay).

    Parameters
    ----------
    goal_x, goal_y:
        Target position from ``goal.pose.pose.position``.
    goal_uuid:
        Unique identifier for the goal (used to build ``task_id``).
    robot_model:
        Robot identifier string (ROS param ``robot_model``).
    max_nav_radius_m:
        Maximum navigation radius treated as the reachability budget.
    safety_buffer_m:
        Safety buffer around keepout zones.
    keepout_zones:
        Pre-loaded list of keepout zone dicts (``id``, ``min_xyz``,
        ``max_xyz``).  Pass the output of :func:`load_keepout_zones`.
    """
    spec: dict[str, Any] = {
        "task_id": f"nav2-{goal_uuid}",
        "meta": {"template": "navigate_to_pose"},
        "substrate": {
            "id": "robot",
            "mass_kg": 0.01,  # nominal — payload gate irrelevant for nav
            "initial_pose": {"xyz": [0.0, 0.0, 0.0]},
        },
        "transformation": {
            "target_pose": {"xyz": [float(goal_x), float(goal_y), 0.0]},
            "tolerance_m": 0.05,
        },
        "constructor": {
            "id": robot_model,
            "base_pose": {"xyz": [0.0, 0.0, 0.0]},
            "max_reach_m": float(max_nav_radius_m),
            "max_payload_kg": 9999.0,
        },
        "allowed_adjustments": {
            "can_move_target": True,
            "can_move_base": False,
            "can_change_constructor": False,
            "can_split_payload": False,
        },
    }

    env: dict[str, Any] = {"safety_buffer": float(safety_buffer_m)}
    if keepout_zones:
        env["keepout_zones"] = keepout_zones
    spec["environment"] = env

    return spec
