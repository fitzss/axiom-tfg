"""Canonical robot registry — profiles, specs, and URDF paths.

Every supported robot is declared once here.  The SDK, codegen layer,
VLA adapter, and server all import from this module instead of
maintaining their own copies.

Usage::

    from axiom_tfg.robots import get_robot, ROBOT_REGISTRY

    profile = get_robot("franka")
    print(profile.max_reach_m)   # 0.855
    print(profile.urdf_path)     # /…/axiom_tfg/data/franka.urdf
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class RobotProfile:
    """Immutable specification of a supported robot."""

    name: str
    max_reach_m: float
    max_payload_kg: float
    dof: int
    base_link: str
    ee_link: str
    urdf_filename: str
    # Joint limits: (lower, upper) in radians for each joint
    joint_position_limits: tuple[tuple[float, float], ...] | None = None
    # Joint velocity limits in rad/s for each joint
    joint_velocity_limits: tuple[float, ...] | None = None
    # Max EE linear speed in m/s (from manufacturer spec)
    max_ee_speed_m_s: float | None = None
    # Default control frequency in Hz
    default_hz: float = 20.0

    @property
    def urdf_path(self) -> str:
        """Absolute path to the bundled URDF file."""
        return str(_DATA_DIR / self.urdf_filename)


ROBOT_REGISTRY: dict[str, RobotProfile] = {
    "ur3e": RobotProfile(
        name="ur3e",
        max_reach_m=0.5,
        max_payload_kg=3.0,
        dof=6,
        base_link="base_link",
        ee_link="ee_link",
        urdf_filename="ur3e.urdf",
    ),
    "ur5e": RobotProfile(
        name="ur5e",
        max_reach_m=1.85,
        max_payload_kg=5.0,
        dof=6,
        base_link="base_link",
        ee_link="ee_link",
        urdf_filename="ur5e.urdf",
    ),
    "ur10e": RobotProfile(
        name="ur10e",
        max_reach_m=1.3,
        max_payload_kg=12.5,
        dof=6,
        base_link="base_link",
        ee_link="ee_link",
        urdf_filename="ur10e.urdf",
    ),
    "franka": RobotProfile(
        name="franka",
        max_reach_m=0.855,
        max_payload_kg=3.0,
        dof=7,
        base_link="panda_link0",
        ee_link="panda_link8",
        urdf_filename="franka.urdf",
        joint_position_limits=(
            (-2.8973, 2.8973),   # joint 1
            (-1.7628, 1.7628),   # joint 2
            (-2.8973, 2.8973),   # joint 3
            (-3.0718, -0.0698),  # joint 4
            (-2.8973, 2.8973),   # joint 5
            (-0.0175, 3.7525),   # joint 6
            (-2.8973, 2.8973),   # joint 7
        ),
        joint_velocity_limits=(2.175, 2.175, 2.175, 2.175, 2.61, 2.61, 2.61),
        max_ee_speed_m_s=1.7,
        default_hz=20.0,
    ),
    "kuka_iiwa14": RobotProfile(
        name="kuka_iiwa14",
        max_reach_m=0.82,
        max_payload_kg=14.0,
        dof=7,
        base_link="iiwa_link_0",
        ee_link="iiwa_link_7",
        urdf_filename="kuka_iiwa14.urdf",
    ),
}


def get_robot(name: str) -> RobotProfile:
    """Look up a robot by name.  Raises ``ValueError`` for unknown robots."""
    try:
        return ROBOT_REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(ROBOT_REGISTRY))
        raise ValueError(
            f"Unknown robot {name!r}. Known robots: {known}"
        ) from None
