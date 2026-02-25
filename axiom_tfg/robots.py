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
