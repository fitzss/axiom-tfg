"""Atlas v0 — feasible transformation space mapping.

Characterizes the boundary between possible and impossible actions for a
given robot + constraints by sampling a grid of end-effector positions and
running IK feasibility at each point.

Outputs:
    - Feasible point cloud (position + feasible/infeasible + margin)
    - Robot overlap report (Robot A vs Robot B)
    - Dataset coverage overlay (trajectory density vs feasible space)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from axiom_tfg.robots import ROBOT_REGISTRY, RobotProfile, get_robot


# ── Data structures ────────────────────────────────────────────────────


@dataclass
class AtlasPoint:
    """A single sampled point in the atlas."""
    xyz: list[float]
    feasible: bool
    margin_m: float  # distance to boundary (positive = inside, negative = outside)
    ik_position_error: float | None = None


@dataclass
class AtlasResult:
    """Result of a feasibility space sampling."""
    robot: str
    base_xyz: list[float]
    resolution_m: float
    bounds_min: list[float]
    bounds_max: list[float]
    total_points: int = 0
    feasible_count: int = 0
    infeasible_count: int = 0
    points: list[AtlasPoint] = field(default_factory=list)

    @property
    def feasible_pct(self) -> float:
        return (self.feasible_count / self.total_points * 100) if self.total_points else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot": self.robot,
            "base_xyz": self.base_xyz,
            "resolution_m": self.resolution_m,
            "bounds_min": self.bounds_min,
            "bounds_max": self.bounds_max,
            "total_points": self.total_points,
            "feasible_count": self.feasible_count,
            "infeasible_count": self.infeasible_count,
            "feasible_pct": round(self.feasible_pct, 1),
        }


@dataclass
class OverlapResult:
    """Result of comparing two robots' feasible spaces."""
    robot_a: str
    robot_b: str
    base_xyz: list[float]
    total_points: int
    both_feasible: int
    only_a: int
    only_b: int
    neither: int

    @property
    def overlap_pct(self) -> float:
        union = self.both_feasible + self.only_a + self.only_b
        return (self.both_feasible / union * 100) if union else 0.0

    @property
    def a_coverage_of_b(self) -> float:
        """% of B's feasible space that A can also reach."""
        b_total = self.both_feasible + self.only_b
        return (self.both_feasible / b_total * 100) if b_total else 0.0

    @property
    def b_coverage_of_a(self) -> float:
        """% of A's feasible space that B can also reach."""
        a_total = self.both_feasible + self.only_a
        return (self.both_feasible / a_total * 100) if a_total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_a": self.robot_a,
            "robot_b": self.robot_b,
            "base_xyz": self.base_xyz,
            "total_points": self.total_points,
            "both_feasible": self.both_feasible,
            "only_a": self.only_a,
            "only_b": self.only_b,
            "neither": self.neither,
            "overlap_pct": round(self.overlap_pct, 1),
            "a_coverage_of_b_pct": round(self.a_coverage_of_b, 1),
            "b_coverage_of_a_pct": round(self.b_coverage_of_a, 1),
        }


@dataclass
class CoverageResult:
    """Result of overlaying a dataset on a robot's feasible space."""
    robot: str
    base_xyz: list[float]
    total_feasible_voxels: int
    occupied_feasible_voxels: int
    total_data_points: int
    data_in_feasible: int
    data_in_infeasible: int

    @property
    def space_coverage_pct(self) -> float:
        """% of feasible space visited by data."""
        return (self.occupied_feasible_voxels / self.total_feasible_voxels * 100) if self.total_feasible_voxels else 0.0

    @property
    def data_feasibility_pct(self) -> float:
        """% of data points that land in feasible space."""
        return (self.data_in_feasible / self.total_data_points * 100) if self.total_data_points else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot": self.robot,
            "base_xyz": self.base_xyz,
            "total_feasible_voxels": self.total_feasible_voxels,
            "occupied_feasible_voxels": self.occupied_feasible_voxels,
            "space_coverage_pct": round(self.space_coverage_pct, 1),
            "total_data_points": self.total_data_points,
            "data_in_feasible": self.data_in_feasible,
            "data_in_infeasible": self.data_in_infeasible,
            "data_feasibility_pct": round(self.data_feasibility_pct, 1),
        }


# ── IK feasibility checker ────────────────────────────────────────────


def _build_ik_chain(profile: RobotProfile):
    """Build an ikpy chain for the given robot profile."""
    import warnings

    import ikpy.chain

    # First build without mask to discover link types
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        chain = ikpy.chain.Chain.from_urdf_file(
        profile.urdf_path,
        base_elements=[profile.base_link],
        last_link_vector=None,
        )
    # Only activate non-fixed links
    mask = [link.joint_type != "fixed" for link in chain.links]
    chain = ikpy.chain.Chain.from_urdf_file(
        profile.urdf_path,
        base_elements=[profile.base_link],
        last_link_vector=None,
        active_links_mask=mask,
    )
    return chain


def _make_initial_position(chain) -> np.ndarray:
    """Create a valid initial guess that respects joint bounds."""
    init = np.zeros(len(chain.links))
    for i, link in enumerate(chain.links):
        if hasattr(link, "bounds") and link.bounds is not None:
            lo, hi = link.bounds
            if lo is not None and hi is not None:
                mid = (lo + hi) / 2
                # If 0 is outside bounds, use midpoint
                if not (lo <= 0 <= hi):
                    init[i] = mid
    return init


def _check_ik(
    chain,
    target_xyz: list[float],
    tolerance: float = 0.01,
    initial_position: np.ndarray | None = None,
) -> tuple[bool, float]:
    """Check IK feasibility. Returns (feasible, position_error)."""
    target = np.array(target_xyz)
    try:
        kwargs: dict[str, Any] = {}
        if initial_position is not None:
            kwargs["initial_position"] = initial_position
        ik_solution = chain.inverse_kinematics(target, **kwargs)
        fk_result = chain.forward_kinematics(ik_solution)
        achieved = fk_result[:3, 3]
        error = float(np.linalg.norm(achieved - target))
        return error <= tolerance, error
    except (ValueError, np.linalg.LinAlgError):
        return False, float("inf")


# ── Core atlas functions ───────────────────────────────────────────────


def sample_feasible_space(
    robot: str,
    base_xyz: list[float] | None = None,
    resolution_m: float = 0.05,
    bounds_min: list[float] | None = None,
    bounds_max: list[float] | None = None,
    tolerance: float = 0.01,
) -> AtlasResult:
    """Sample a 3D grid and check IK feasibility at each point.

    Parameters
    ----------
    robot : str
        Robot profile name from registry.
    base_xyz : list[float], optional
        Robot base position [x, y, z]. Defaults to [0, 0, 0].
    resolution_m : float
        Grid spacing in metres (default 50mm).
    bounds_min / bounds_max : list[float], optional
        Sampling volume. Defaults to reach-based cube around base.
    tolerance : float
        IK position error tolerance in metres.
    """
    profile = get_robot(robot)
    base = np.array(base_xyz or [0.0, 0.0, 0.0])
    reach = profile.max_reach_m

    if bounds_min is None:
        bounds_min = (base - reach).tolist()
    if bounds_max is None:
        bounds_max = (base + reach).tolist()

    chain = _build_ik_chain(profile)
    init_pos = _make_initial_position(chain)

    # Generate grid
    xs = np.arange(bounds_min[0], bounds_max[0] + resolution_m / 2, resolution_m)
    ys = np.arange(bounds_min[1], bounds_max[1] + resolution_m / 2, resolution_m)
    zs = np.arange(bounds_min[2], bounds_max[2] + resolution_m / 2, resolution_m)

    result = AtlasResult(
        robot=robot,
        base_xyz=base.tolist(),
        resolution_m=resolution_m,
        bounds_min=bounds_min,
        bounds_max=bounds_max,
    )

    # Pre-filter: skip points outside spherical reach (fast cull)
    for x in xs:
        for y in ys:
            for z in zs:
                point = [float(x), float(y), float(z)]
                dist = math.sqrt(sum((p - b) ** 2 for p, b in zip(point, base.tolist())))

                if dist > reach * 1.1:
                    # Clearly outside — skip IK, mark infeasible
                    result.points.append(AtlasPoint(
                        xyz=point,
                        feasible=False,
                        margin_m=-(dist - reach),
                    ))
                    result.infeasible_count += 1
                    result.total_points += 1
                    continue

                # Run IK
                feasible, error = _check_ik(chain, point, tolerance, init_pos)
                margin = tolerance - error if feasible else -(error - tolerance)

                result.points.append(AtlasPoint(
                    xyz=point,
                    feasible=feasible,
                    margin_m=round(margin, 6),
                    ik_position_error=round(error, 6),
                ))

                if feasible:
                    result.feasible_count += 1
                else:
                    result.infeasible_count += 1
                result.total_points += 1

    return result


def compute_overlap(
    robot_a: str,
    robot_b: str,
    base_xyz: list[float] | None = None,
    resolution_m: float = 0.05,
    tolerance: float = 0.01,
) -> OverlapResult:
    """Compare feasible spaces of two robots at the same base position."""
    profile_a = get_robot(robot_a)
    profile_b = get_robot(robot_b)
    base = base_xyz or [0.0, 0.0, 0.0]

    # Use the larger reach for bounds
    max_reach = max(profile_a.max_reach_m, profile_b.max_reach_m)
    base_arr = np.array(base)
    bounds_min = (base_arr - max_reach).tolist()
    bounds_max = (base_arr + max_reach).tolist()

    chain_a = _build_ik_chain(profile_a)
    chain_b = _build_ik_chain(profile_b)
    init_a = _make_initial_position(chain_a)
    init_b = _make_initial_position(chain_b)

    xs = np.arange(bounds_min[0], bounds_max[0] + resolution_m / 2, resolution_m)
    ys = np.arange(bounds_min[1], bounds_max[1] + resolution_m / 2, resolution_m)
    zs = np.arange(bounds_min[2], bounds_max[2] + resolution_m / 2, resolution_m)

    both = only_a = only_b = neither = 0

    for x in xs:
        for y in ys:
            for z in zs:
                point = [float(x), float(y), float(z)]

                # Quick spherical pre-filter
                dist_from_base = math.sqrt(sum((p - b) ** 2 for p, b in zip(point, base)))
                a_in_sphere = dist_from_base <= profile_a.max_reach_m * 1.1
                b_in_sphere = dist_from_base <= profile_b.max_reach_m * 1.1

                a_ok = False
                b_ok = False

                if a_in_sphere:
                    a_ok, _ = _check_ik(chain_a, point, tolerance, init_a)
                if b_in_sphere:
                    b_ok, _ = _check_ik(chain_b, point, tolerance, init_b)

                if a_ok and b_ok:
                    both += 1
                elif a_ok:
                    only_a += 1
                elif b_ok:
                    only_b += 1
                else:
                    neither += 1

    total = both + only_a + only_b + neither
    return OverlapResult(
        robot_a=robot_a,
        robot_b=robot_b,
        base_xyz=base,
        total_points=total,
        both_feasible=both,
        only_a=only_a,
        only_b=only_b,
        neither=neither,
    )


def compute_coverage(
    atlas: AtlasResult,
    ee_positions: np.ndarray,
) -> CoverageResult:
    """Overlay dataset EE positions on a robot's atlas.

    Parameters
    ----------
    atlas : AtlasResult
        Pre-computed atlas from ``sample_feasible_space``.
    ee_positions : np.ndarray
        Array of shape (N, 3) with EE xyz positions.
    """
    res = atlas.resolution_m

    # Build a set of feasible voxel keys
    feasible_voxels: set[tuple[int, int, int]] = set()
    for pt in atlas.points:
        if pt.feasible:
            key = (
                round(pt.xyz[0] / res),
                round(pt.xyz[1] / res),
                round(pt.xyz[2] / res),
            )
            feasible_voxels.add(key)

    # Map data points to voxels
    occupied_feasible: set[tuple[int, int, int]] = set()
    data_in_feasible = 0
    data_in_infeasible = 0

    for i in range(len(ee_positions)):
        pos = ee_positions[i]
        key = (
            round(float(pos[0]) / res),
            round(float(pos[1]) / res),
            round(float(pos[2]) / res),
        )
        if key in feasible_voxels:
            data_in_feasible += 1
            occupied_feasible.add(key)
        else:
            data_in_infeasible += 1

    return CoverageResult(
        robot=atlas.robot,
        base_xyz=atlas.base_xyz,
        total_feasible_voxels=len(feasible_voxels),
        occupied_feasible_voxels=len(occupied_feasible),
        total_data_points=len(ee_positions),
        data_in_feasible=data_in_feasible,
        data_in_infeasible=data_in_infeasible,
    )


# ── File I/O ───────────────────────────────────────────────────────────


def write_atlas(atlas: AtlasResult, out_dir: Path) -> Path:
    """Write atlas results to a directory."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Summary
    summary_path = out_dir / "atlas_summary.json"
    summary_path.write_text(
        json.dumps(atlas.to_dict(), indent=2) + "\n", encoding="utf-8"
    )

    # Point cloud as JSONL (for large datasets)
    cloud_path = out_dir / "atlas_points.jsonl"
    with open(cloud_path, "w", encoding="utf-8") as f:
        for pt in atlas.points:
            row = {
                "xyz": [round(c, 4) for c in pt.xyz],
                "feasible": pt.feasible,
                "margin_m": pt.margin_m,
            }
            if pt.ik_position_error is not None:
                row["ik_error"] = pt.ik_position_error
            f.write(json.dumps(row) + "\n")

    # Also write a compact numpy-friendly CSV
    csv_path = out_dir / "atlas_points.csv"
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("x,y,z,feasible,margin_m\n")
        for pt in atlas.points:
            f.write(f"{pt.xyz[0]:.4f},{pt.xyz[1]:.4f},{pt.xyz[2]:.4f},"
                    f"{int(pt.feasible)},{pt.margin_m:.6f}\n")

    return summary_path


def write_overlap(overlap: OverlapResult, out_dir: Path) -> Path:
    """Write overlap report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "overlap_report.json"
    path.write_text(
        json.dumps(overlap.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return path


def write_coverage(coverage: CoverageResult, out_dir: Path) -> Path:
    """Write coverage report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "coverage_report.json"
    path.write_text(
        json.dumps(coverage.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return path
