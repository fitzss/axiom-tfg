"""Physical margin audit for robot trajectory data.

Analyzes rollout trajectories (from LeRobot, JSONL logs, or any source)
against physical constraints and produces structured evidence artifacts.

Checks performed:
- **Reach**: Euclidean distance proxy + IK feasibility via URDF/ikpy
- **EE velocity/jerk**: Cartesian speed from consecutive positions
- **Joint limits**: position, velocity, acceleration (when joint data provided)
- **Keepout zones**: AABB containment with safety buffers

Usage::

    from axiom_tfg.audit import audit_trajectory, AuditConfig

    config = AuditConfig(
        robot="franka",
        base_xyz=[0.0, 0.0, 0.91],
        control_hz=20.0,
    )
    report = audit_trajectory(ee_positions, config)
    print(report.summary())
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from axiom_tfg.robots import ROBOT_REGISTRY, RobotProfile


# ── Configuration ────────────────────────────────────────────────────────


@dataclass
class KeepoutSpec:
    """Axis-aligned bounding box defining a forbidden volume."""

    id: str
    min_xyz: list[float]
    max_xyz: list[float]
    safety_buffer: float = 0.02


@dataclass
class AuditConfig:
    """Configuration for a trajectory audit."""

    robot: str = "franka"
    base_xyz: list[float] | None = None
    keepout_zones: list[KeepoutSpec] | None = None
    reach_warning_pct: float = 0.90
    reach_critical_pct: float = 0.95
    control_hz: float | None = None  # None = use robot default
    ik_check_worst_n: int = 50  # IK-check the N worst reach-margin steps

    @property
    def profile(self) -> RobotProfile:
        return ROBOT_REGISTRY[self.robot]

    @property
    def base(self) -> np.ndarray:
        if self.base_xyz is not None:
            return np.array(self.base_xyz, dtype=np.float64)
        return np.zeros(3, dtype=np.float64)

    @property
    def dt(self) -> float:
        hz = self.control_hz or self.profile.default_hz
        return 1.0 / hz


# ── Per-step evidence ────────────────────────────────────────────────────


@dataclass
class StepEvidence:
    """Margin measurements for a single timestep."""

    step: int
    episode: int
    ee_xyz: list[float]
    dist_from_base: float
    reach_margin: float  # positive = inside, negative = beyond
    reach_margin_pct: float  # margin as fraction of max reach
    keepout_margins: dict[str, float]  # zone_id -> signed distance
    keepout_violated: list[str]  # zone IDs where EE is inside
    ee_speed: float | None = None  # m/s, Cartesian EE speed
    ee_jerk: float | None = None  # m/s^3, Cartesian jerk magnitude
    joint_velocities: list[float] | None = None  # rad/s per joint
    joint_vel_margins: list[float] | None = None  # margin to limit per joint
    ik_feasible: bool | None = None  # None = not checked


# ── Flagged moment ───────────────────────────────────────────────────────


@dataclass
class FlaggedMoment:
    """A step that warrants attention — near or beyond a constraint."""

    step: int
    episode: int
    ee_xyz: list[float]
    flag_type: str
    constraint: str  # which constraint triggered
    margin: float  # how far from the boundary (negative = violated)
    detail: str  # human-readable description


# ── Audit report ─────────────────────────────────────────────────────────


@dataclass
class AuditReport:
    """Full audit result for a trajectory dataset."""

    config: AuditConfig
    total_steps: int
    total_episodes: int
    steps_evidence: list[StepEvidence]
    flagged: list[FlaggedMoment]

    # Aggregate reach stats
    reach_min_margin: float = 0.0
    reach_mean_margin: float = 0.0
    reach_violations: int = 0
    reach_warnings: int = 0
    reach_critical: int = 0

    # IK feasibility stats
    ik_checks_performed: int = 0
    ik_infeasible: int = 0

    # EE velocity stats
    ee_speed_max: float = 0.0
    ee_speed_mean: float = 0.0
    ee_speed_violations: int = 0  # exceeds robot max EE speed
    ee_jerk_max: float = 0.0
    ee_jerk_mean: float = 0.0

    # Joint dynamics stats (only populated if joint data provided)
    joint_vel_violations: int = 0
    joint_vel_violations_by_joint: dict[int, int] = field(default_factory=dict)
    joint_pos_violations: int = 0

    # Aggregate keepout stats
    keepout_violations: int = 0
    keepout_violations_by_zone: dict[str, int] = field(default_factory=dict)

    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def summary(self) -> str:
        """Human-readable summary report."""
        profile = self.config.profile
        hz = self.config.control_hz or profile.default_hz
        lines = [
            "",
            f"  Axiom Physical Margin Audit",
            f"  {'=' * 50}",
            f"  Robot: {profile.name} (reach={profile.max_reach_m}m, payload={profile.max_payload_kg}kg, {profile.dof}DOF)",
            f"  Base position: {self.config.base_xyz or [0,0,0]}",
            f"  Control rate: {hz}Hz (dt={self.config.dt*1000:.1f}ms)",
            f"  Episodes: {self.total_episodes}  Steps: {self.total_steps}",
            f"",
            f"  Reach Analysis",
            f"  {'-' * 40}",
            f"  Min margin:  {self.reach_min_margin:+.4f}m",
            f"  Mean margin: {self.reach_mean_margin:+.4f}m",
            f"  Violations (beyond reach): {self.reach_violations} / {self.total_steps} ({self._pct(self.reach_violations)}%)",
            f"  Critical (<{self.config.reach_critical_pct*100:.0f}% margin): {self.reach_critical} / {self.total_steps} ({self._pct(self.reach_critical)}%)",
            f"  Warnings  (<{self.config.reach_warning_pct*100:.0f}% margin): {self.reach_warnings} / {self.total_steps} ({self._pct(self.reach_warnings)}%)",
        ]

        if self.ik_checks_performed > 0:
            lines += [
                f"",
                f"  IK Feasibility (checked {self.ik_checks_performed} worst-margin steps)",
                f"  {'-' * 40}",
                f"  Infeasible poses: {self.ik_infeasible} / {self.ik_checks_performed} ({self._pct(self.ik_infeasible, self.ik_checks_performed)}%)",
            ]

        if self.ee_speed_max > 0:
            lines += [
                f"",
                f"  EE Velocity Analysis",
                f"  {'-' * 40}",
                f"  Max EE speed:  {self.ee_speed_max:.4f} m/s",
                f"  Mean EE speed: {self.ee_speed_mean:.4f} m/s",
            ]
            if profile.max_ee_speed_m_s:
                lines.append(
                    f"  Speed violations (>{profile.max_ee_speed_m_s} m/s): "
                    f"{self.ee_speed_violations} / {self.total_steps} ({self._pct(self.ee_speed_violations)}%)"
                )
            lines += [
                f"  Max jerk:  {self.ee_jerk_max:.2f} m/s\u00b3",
                f"  Mean jerk: {self.ee_jerk_mean:.2f} m/s\u00b3",
            ]

        if self.joint_vel_violations > 0 or self.joint_pos_violations > 0:
            lines += [
                f"",
                f"  Joint Dynamics Analysis",
                f"  {'-' * 40}",
                f"  Joint position limit violations: {self.joint_pos_violations}",
                f"  Joint velocity limit violations: {self.joint_vel_violations}",
            ]
            for j, count in sorted(self.joint_vel_violations_by_joint.items()):
                if count > 0:
                    lines.append(f"    Joint {j}: {count} velocity violations")

        if self.config.keepout_zones:
            lines += [
                f"",
                f"  Keepout Zone Analysis",
                f"  {'-' * 40}",
                f"  Total violations: {self.keepout_violations} / {self.total_steps} ({self._pct(self.keepout_violations)}%)",
            ]
            for zone_id, count in self.keepout_violations_by_zone.items():
                lines.append(f"    {zone_id}: {count} violations")

        if self.flagged:
            lines += [
                f"",
                f"  Flagged Moments ({len(self.flagged)} total)",
                f"  {'-' * 40}",
            ]
            for fm in self.flagged[:10]:
                lines.append(
                    f"    ep={fm.episode} t={fm.step} [{fm.flag_type}] "
                    f"margin={fm.margin:+.4f}m  {fm.detail}"
                )
            if len(self.flagged) > 10:
                lines.append(f"    ... and {len(self.flagged) - 10} more")

        lines.append("")
        return "\n".join(lines)

    def _pct(self, count: int, total: int | None = None) -> str:
        t = total if total is not None else self.total_steps
        if t == 0:
            return "0.00"
        return f"{100 * count / t:.2f}"

    def to_dict(self) -> dict[str, Any]:
        """Structured output for JSON serialization."""
        profile = self.config.profile
        return {
            "audit_version": "0.2.0",
            "created_at": self.created_at,
            "config": {
                "robot": self.config.robot,
                "base_xyz": self.config.base_xyz or [0, 0, 0],
                "max_reach_m": profile.max_reach_m,
                "max_payload_kg": profile.max_payload_kg,
                "max_ee_speed_m_s": profile.max_ee_speed_m_s,
                "dof": profile.dof,
                "control_hz": self.config.control_hz or profile.default_hz,
                "keepout_zones": [
                    {"id": z.id, "min_xyz": z.min_xyz, "max_xyz": z.max_xyz}
                    for z in (self.config.keepout_zones or [])
                ],
                "reach_warning_pct": self.config.reach_warning_pct,
                "reach_critical_pct": self.config.reach_critical_pct,
            },
            "summary": {
                "total_steps": self.total_steps,
                "total_episodes": self.total_episodes,
                "reach": {
                    "min_margin_m": round(self.reach_min_margin, 6),
                    "mean_margin_m": round(self.reach_mean_margin, 6),
                    "violations": self.reach_violations,
                    "critical": self.reach_critical,
                    "warnings": self.reach_warnings,
                },
                "ik_feasibility": {
                    "checks_performed": self.ik_checks_performed,
                    "infeasible": self.ik_infeasible,
                },
                "ee_velocity": {
                    "max_speed_m_s": round(self.ee_speed_max, 6),
                    "mean_speed_m_s": round(self.ee_speed_mean, 6),
                    "speed_violations": self.ee_speed_violations,
                    "max_jerk_m_s3": round(self.ee_jerk_max, 4),
                    "mean_jerk_m_s3": round(self.ee_jerk_mean, 4),
                },
                "joint_dynamics": {
                    "position_violations": self.joint_pos_violations,
                    "velocity_violations": self.joint_vel_violations,
                    "velocity_violations_by_joint": self.joint_vel_violations_by_joint,
                },
                "keepout": {
                    "total_violations": self.keepout_violations,
                    "by_zone": self.keepout_violations_by_zone,
                },
            },
            "flagged_moments": [
                {
                    "step": fm.step,
                    "episode": fm.episode,
                    "ee_xyz": fm.ee_xyz,
                    "flag_type": fm.flag_type,
                    "constraint": fm.constraint,
                    "margin": round(fm.margin, 6),
                    "detail": fm.detail,
                }
                for fm in self.flagged
            ],
        }


# ── Keepout margin computation ───────────────────────────────────────────


def _keepout_margin(point: np.ndarray, zone: KeepoutSpec) -> float:
    """Compute signed distance from point to keepout zone boundary.

    Positive = outside (safe), negative = inside (violated).
    """
    lo = np.array(zone.min_xyz) - zone.safety_buffer
    hi = np.array(zone.max_xyz) + zone.safety_buffer

    inside = np.all((point >= lo) & (point <= hi))

    if not inside:
        clamped = np.clip(point, lo, hi)
        return float(np.linalg.norm(point - clamped))

    d_lo = point - lo
    d_hi = hi - point
    min_escape = float(np.min(np.concatenate([d_lo, d_hi])))
    return -min_escape


# ── EE velocity / jerk computation ───────────────────────────────────────


def _compute_ee_velocities(
    ee_positions: np.ndarray,
    episodes: np.ndarray,
    dt: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute per-step EE speed and jerk magnitude.

    Returns:
        speeds: (N,) array of EE speed in m/s (NaN for episode boundaries).
        jerks: (N,) array of jerk magnitude in m/s^3 (NaN where undefined).
    """
    n = len(ee_positions)
    speeds = np.full(n, np.nan)
    jerks = np.full(n, np.nan)

    for ep in np.unique(episodes):
        mask = episodes == ep
        idx = np.where(mask)[0]
        if len(idx) < 2:
            continue

        # Velocities: v[i] = (pos[i+1] - pos[i]) / dt
        pos = ee_positions[idx]
        vel = np.diff(pos, axis=0) / dt  # (M-1, 3)
        speed = np.linalg.norm(vel, axis=1)  # (M-1,)
        # Assign speed to the step that *starts* the motion
        speeds[idx[:-1]] = speed

        # Acceleration: a[i] = (v[i+1] - v[i]) / dt
        if len(vel) >= 2:
            acc = np.diff(vel, axis=0) / dt  # (M-2, 3)
            # Jerk: j[i] = (a[i+1] - a[i]) / dt
            if len(acc) >= 2:
                jerk = np.diff(acc, axis=0) / dt  # (M-3, 3)
                jerk_mag = np.linalg.norm(jerk, axis=1)
                jerks[idx[:len(jerk_mag)]] = jerk_mag

    return speeds, jerks


# ── Joint dynamics validation ────────────────────────────────────────────


def _audit_joint_dynamics(
    joint_positions: np.ndarray,
    episodes: np.ndarray,
    profile: RobotProfile,
    dt: float,
) -> dict[str, Any]:
    """Validate joint positions and velocities against robot limits.

    Args:
        joint_positions: (N, DOF) joint angles in radians.
        episodes: (N,) episode indices.
        profile: Robot profile with joint limits.
        dt: Timestep in seconds.

    Returns:
        Dict with per-step joint velocities, violation counts, and flagged info.
    """
    n, dof = joint_positions.shape
    result: dict[str, Any] = {
        "joint_velocities": [None] * n,
        "joint_vel_margins": [None] * n,
        "pos_violations": 0,
        "vel_violations": 0,
        "vel_violations_by_joint": {j: 0 for j in range(dof)},
        "flags": [],
    }

    pos_limits = profile.joint_position_limits
    vel_limits = profile.joint_velocity_limits

    # Check joint position limits
    if pos_limits and len(pos_limits) >= dof:
        for i in range(n):
            for j in range(dof):
                lo, hi = pos_limits[j]
                q = joint_positions[i, j]
                if q < lo or q > hi:
                    result["pos_violations"] += 1
                    margin = min(q - lo, hi - q)  # negative when violated
                    result["flags"].append({
                        "step": i,
                        "flag_type": "JOINT_POS_VIOLATION",
                        "constraint": f"joint_{j}_position",
                        "margin": margin,
                        "detail": f"Joint {j} at {q:.4f} rad, limits [{lo:.4f}, {hi:.4f}]",
                    })
                    break  # one flag per step

    # Compute and check joint velocities
    if vel_limits and len(vel_limits) >= dof:
        for ep in np.unique(episodes):
            mask = episodes == ep
            idx = np.where(mask)[0]
            if len(idx) < 2:
                continue

            q = joint_positions[idx]
            qdot = np.diff(q, axis=0) / dt  # (M-1, DOF)

            for k in range(len(qdot)):
                step_idx = idx[k]
                vel_list = qdot[k].tolist()
                margins = []
                result["joint_velocities"][step_idx] = vel_list

                for j in range(dof):
                    v = abs(qdot[k, j])
                    limit = vel_limits[j]
                    margin = limit - v
                    margins.append(margin)

                    if v > limit:
                        result["vel_violations"] += 1
                        result["vel_violations_by_joint"][j] += 1
                        result["flags"].append({
                            "step": step_idx,
                            "flag_type": "JOINT_VEL_VIOLATION",
                            "constraint": f"joint_{j}_velocity",
                            "margin": -float(v - limit),
                            "detail": (
                                f"Joint {j} velocity {v:.3f} rad/s "
                                f"exceeds limit {limit:.3f} rad/s"
                            ),
                        })

                result["joint_vel_margins"][step_idx] = margins

    return result


# ── IK feasibility check ────────────────────────────────────────────────


def _check_ik_feasibility(
    ee_positions: np.ndarray,
    step_indices: list[int],
    profile: RobotProfile,
    base_xyz: np.ndarray,
) -> dict[int, bool]:
    """Check IK feasibility for specific steps using ikpy.

    Returns:
        Dict mapping step index -> bool (True = feasible).
    """
    try:
        import ikpy.chain
    except ImportError:
        return {}

    urdf_path = profile.urdf_path
    if not Path(urdf_path).exists():
        return {}

    chain = ikpy.chain.Chain.from_urdf_file(
        urdf_path,
        base_elements=[profile.base_link],
        active_links_mask=[False] + [True] * profile.dof + [False],
    )

    results: dict[int, bool] = {}
    for idx in step_indices:
        ee = ee_positions[idx]
        target = ee - base_xyz  # IK in robot base frame
        target_4x4 = np.eye(4)
        target_4x4[:3, 3] = target

        try:
            ik_solution = chain.inverse_kinematics_frame(
                target_4x4,
                max_iter=100,
            )
            # Verify: compute FK and check distance to target
            fk = chain.forward_kinematics(ik_solution)
            fk_pos = fk[:3, 3]
            error = float(np.linalg.norm(fk_pos - target))
            results[idx] = error < 0.01  # 1cm tolerance
        except Exception:
            results[idx] = False

    return results


# ── Core audit function ──────────────────────────────────────────────────


def audit_trajectory(
    ee_positions: np.ndarray,
    config: AuditConfig,
    *,
    actions: np.ndarray | None = None,
    next_ee_positions: np.ndarray | None = None,
    episodes: np.ndarray | None = None,
    joint_positions: np.ndarray | None = None,
    run_ik: bool = False,
) -> AuditReport:
    """Audit a trajectory against physical constraints.

    Args:
        ee_positions: (N, 3) array of end-effector positions per step.
        config: Audit configuration (robot, base, keepout zones).
        actions: Optional (N, 3+) action deltas (kept for API compat, not used in v0.2).
        next_ee_positions: Optional (N, 3) next EE positions (kept for API compat).
        episodes: (N,) array of episode indices per step.
        joint_positions: Optional (N, DOF) joint angles in radians.
        run_ik: Whether to IK-check worst reach-margin steps.

    Returns:
        AuditReport with per-step evidence, flagged moments, and summary stats.
    """
    n = len(ee_positions)
    if episodes is None:
        episodes = np.zeros(n, dtype=np.int64)

    profile = config.profile
    base = config.base
    zones = config.keepout_zones or []
    dt = config.dt

    # ── Compute EE velocities and jerk ──
    speeds, jerks = _compute_ee_velocities(ee_positions, episodes, dt)

    # ── Compute joint dynamics if available ──
    joint_result = None
    if joint_positions is not None and joint_positions.shape[0] == n:
        joint_result = _audit_joint_dynamics(joint_positions, episodes, profile, dt)

    # ── Per-step analysis ──
    steps_evidence: list[StepEvidence] = []
    flagged: list[FlaggedMoment] = []
    reach_margins: list[float] = []
    keepout_violation_counts: dict[str, int] = {z.id: 0 for z in zones}
    total_keepout_violations = 0

    for i in range(n):
        ee = ee_positions[i]
        ep = int(episodes[i])

        # Reach margin
        dist = float(np.linalg.norm(ee - base))
        margin = profile.max_reach_m - dist
        margin_pct = margin / profile.max_reach_m if profile.max_reach_m > 0 else 0.0
        reach_margins.append(margin)

        # Keepout margins
        k_margins: dict[str, float] = {}
        k_violated: list[str] = []
        for zone in zones:
            km = _keepout_margin(ee, zone)
            k_margins[zone.id] = km
            if km < 0:
                k_violated.append(zone.id)
                keepout_violation_counts[zone.id] += 1
                total_keepout_violations += 1

        step_ev = StepEvidence(
            step=i,
            episode=ep,
            ee_xyz=ee.tolist(),
            dist_from_base=dist,
            reach_margin=margin,
            reach_margin_pct=margin_pct,
            keepout_margins=k_margins,
            keepout_violated=k_violated,
            ee_speed=float(speeds[i]) if not np.isnan(speeds[i]) else None,
            ee_jerk=float(jerks[i]) if not np.isnan(jerks[i]) else None,
            joint_velocities=(
                joint_result["joint_velocities"][i] if joint_result else None
            ),
            joint_vel_margins=(
                joint_result["joint_vel_margins"][i] if joint_result else None
            ),
        )
        steps_evidence.append(step_ev)

        # Flag reach issues
        if margin < 0:
            flagged.append(FlaggedMoment(
                step=i, episode=ep, ee_xyz=ee.tolist(),
                flag_type="REACH_VIOLATION",
                constraint="reach",
                margin=margin,
                detail=f"EE at {dist:.3f}m, limit {profile.max_reach_m}m (exceeded by {-margin:.3f}m)",
            ))
        elif margin_pct < (1.0 - config.reach_critical_pct):
            flagged.append(FlaggedMoment(
                step=i, episode=ep, ee_xyz=ee.tolist(),
                flag_type="REACH_CRITICAL",
                constraint="reach",
                margin=margin,
                detail=f"EE at {dist:.3f}m, limit {profile.max_reach_m}m ({margin_pct*100:.1f}% margin)",
            ))
        elif margin_pct < (1.0 - config.reach_warning_pct):
            flagged.append(FlaggedMoment(
                step=i, episode=ep, ee_xyz=ee.tolist(),
                flag_type="REACH_WARNING",
                constraint="reach",
                margin=margin,
                detail=f"EE at {dist:.3f}m, limit {profile.max_reach_m}m ({margin_pct*100:.1f}% margin)",
            ))

        # Flag EE speed violations
        if not np.isnan(speeds[i]) and profile.max_ee_speed_m_s:
            if speeds[i] > profile.max_ee_speed_m_s:
                overshoot = speeds[i] - profile.max_ee_speed_m_s
                flagged.append(FlaggedMoment(
                    step=i, episode=ep, ee_xyz=ee.tolist(),
                    flag_type="EE_SPEED_VIOLATION",
                    constraint="ee_speed",
                    margin=-overshoot,
                    detail=(
                        f"EE speed {speeds[i]:.3f} m/s exceeds "
                        f"limit {profile.max_ee_speed_m_s} m/s"
                    ),
                ))

        # Flag keepout violations
        for zone_id in k_violated:
            flagged.append(FlaggedMoment(
                step=i, episode=ep, ee_xyz=ee.tolist(),
                flag_type="KEEPOUT_VIOLATION",
                constraint=zone_id,
                margin=k_margins[zone_id],
                detail=f"EE inside zone '{zone_id}' (depth {-k_margins[zone_id]:.3f}m)",
            ))

    # ── Add joint dynamics flags ──
    if joint_result:
        for jf in joint_result["flags"]:
            ep = int(episodes[jf["step"]])
            ee = ee_positions[jf["step"]]
            flagged.append(FlaggedMoment(
                step=jf["step"],
                episode=ep,
                ee_xyz=ee.tolist(),
                flag_type=jf["flag_type"],
                constraint=jf["constraint"],
                margin=jf["margin"],
                detail=jf["detail"],
            ))

    # ── IK feasibility on worst-margin steps ──
    ik_checks = 0
    ik_infeasible = 0
    if run_ik and n > 0:
        worst_indices = sorted(
            range(n), key=lambda i: reach_margins[i]
        )[:config.ik_check_worst_n]
        ik_results = _check_ik_feasibility(
            ee_positions, worst_indices, profile, base,
        )
        ik_checks = len(ik_results)
        for idx, feasible in ik_results.items():
            steps_evidence[idx].ik_feasible = feasible
            if not feasible:
                ik_infeasible += 1
                ep = int(episodes[idx])
                ee = ee_positions[idx]
                dist = float(np.linalg.norm(ee - base))
                flagged.append(FlaggedMoment(
                    step=idx,
                    episode=ep,
                    ee_xyz=ee.tolist(),
                    flag_type="IK_INFEASIBLE",
                    constraint="ik_feasibility",
                    margin=reach_margins[idx],
                    detail=(
                        f"No IK solution at {dist:.3f}m from base "
                        f"(within Euclidean reach but kinematically infeasible)"
                    ),
                ))

    # Sort flagged by severity (most negative margin first)
    flagged.sort(key=lambda fm: fm.margin)

    # ── Aggregate stats ──
    margins_arr = np.array(reach_margins)
    unique_eps = len(set(int(e) for e in episodes))

    valid_speeds = speeds[~np.isnan(speeds)]
    valid_jerks = jerks[~np.isnan(jerks)]

    ee_speed_violations = 0
    if profile.max_ee_speed_m_s and len(valid_speeds) > 0:
        ee_speed_violations = int((valid_speeds > profile.max_ee_speed_m_s).sum())

    report = AuditReport(
        config=config,
        total_steps=n,
        total_episodes=unique_eps,
        steps_evidence=steps_evidence,
        flagged=flagged,
        reach_min_margin=float(margins_arr.min()) if n > 0 else 0.0,
        reach_mean_margin=float(margins_arr.mean()) if n > 0 else 0.0,
        reach_violations=int((margins_arr < 0).sum()),
        reach_critical=int((margins_arr < profile.max_reach_m * (1 - config.reach_critical_pct)).sum()),
        reach_warnings=int((margins_arr < profile.max_reach_m * (1 - config.reach_warning_pct)).sum()),
        ik_checks_performed=ik_checks,
        ik_infeasible=ik_infeasible,
        ee_speed_max=float(valid_speeds.max()) if len(valid_speeds) > 0 else 0.0,
        ee_speed_mean=float(valid_speeds.mean()) if len(valid_speeds) > 0 else 0.0,
        ee_speed_violations=ee_speed_violations,
        ee_jerk_max=float(valid_jerks.max()) if len(valid_jerks) > 0 else 0.0,
        ee_jerk_mean=float(valid_jerks.mean()) if len(valid_jerks) > 0 else 0.0,
        joint_vel_violations=(
            joint_result["vel_violations"] if joint_result else 0
        ),
        joint_vel_violations_by_joint=(
            joint_result["vel_violations_by_joint"] if joint_result else {}
        ),
        joint_pos_violations=(
            joint_result["pos_violations"] if joint_result else 0
        ),
        keepout_violations=total_keepout_violations,
        keepout_violations_by_zone=keepout_violation_counts,
    )

    return report


# ── I/O helpers ──────────────────────────────────────────────────────────


def write_audit_report(report: AuditReport, out_dir: Path) -> Path:
    """Write audit artifacts to a directory."""
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "audit_report.json"
    report_path.write_text(
        json.dumps(report.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )

    if report.flagged:
        flagged_path = out_dir / "flagged_moments.jsonl"
        with open(flagged_path, "w", encoding="utf-8") as f:
            for fm in report.flagged:
                f.write(json.dumps({
                    "step": fm.step,
                    "episode": fm.episode,
                    "ee_xyz": fm.ee_xyz,
                    "flag_type": fm.flag_type,
                    "constraint": fm.constraint,
                    "margin": round(fm.margin, 6),
                    "detail": fm.detail,
                }) + "\n")

    summary_path = out_dir / "audit_summary.txt"
    summary_path.write_text(report.summary(), encoding="utf-8")

    return report_path


# ── LeRobot dataset loader ───────────────────────────────────────────────


def load_lerobot_trajectory(
    repo_id: str,
    *,
    max_episodes: int | None = None,
    state_key: str = "observation.state",
    action_key: str = "action",
    ee_slice: tuple[int, int] = (0, 3),
    action_pos_slice: tuple[int, int] = (0, 3),
) -> dict[str, np.ndarray]:
    """Load trajectory data from a LeRobot dataset on HuggingFace Hub.

    Args:
        repo_id: HF dataset ID (e.g. "lerobot/libero_10").
        max_episodes: Limit number of episodes loaded (None = all).
        state_key: Column name for observation state.
        action_key: Column name for actions.
        ee_slice: (start, end) indices into state vector for EE position.
        action_pos_slice: (start, end) indices into action vector for position deltas.

    Returns:
        Dict with keys: "ee_positions", "actions", "episodes", "frames",
        "next_ee_positions".
    """
    from huggingface_hub import HfApi, hf_hub_download
    import pyarrow.parquet as pq

    api = HfApi()

    # List data files (recurse into chunk directories)
    data_files: list[str] = []
    tree = list(api.list_repo_tree(repo_id, repo_type="dataset", path_in_repo="data"))
    chunk_dirs = [f.path for f in tree if not f.path.endswith(".parquet")]
    data_files.extend(sorted(f.path for f in tree if f.path.endswith(".parquet")))
    for chunk_dir in sorted(chunk_dirs):
        subtree = list(api.list_repo_tree(repo_id, repo_type="dataset", path_in_repo=chunk_dir))
        data_files.extend(sorted(f.path for f in subtree if f.path.endswith(".parquet")))

    all_state = []
    all_action = []
    all_episode = []
    all_frame = []
    episodes_seen: set[int] = set()

    for file_path in data_files:
        if max_episodes is not None and len(episodes_seen) >= max_episodes:
            break

        local = hf_hub_download(repo_id, file_path, repo_type="dataset")
        table = pq.read_table(local)

        state = np.array(table.column(state_key).to_pylist(), dtype=np.float64)
        action = np.array(table.column(action_key).to_pylist(), dtype=np.float64)
        episode = np.array(table.column("episode_index").to_pylist(), dtype=np.int64)
        frame = np.array(table.column("frame_index").to_pylist(), dtype=np.int64)

        if max_episodes is not None:
            new_eps = set(episode.tolist())
            episodes_seen.update(new_eps)
            if len(episodes_seen) > max_episodes:
                keep_eps = sorted(episodes_seen)[:max_episodes]
                mask = np.isin(episode, keep_eps)
                state = state[mask]
                action = action[mask]
                episode = episode[mask]
                frame = frame[mask]
                episodes_seen = set(keep_eps)

        all_state.append(state)
        all_action.append(action)
        all_episode.append(episode)
        all_frame.append(frame)

    state = np.concatenate(all_state)
    action = np.concatenate(all_action)
    episodes = np.concatenate(all_episode)
    frames = np.concatenate(all_frame)

    ee_start, ee_end = ee_slice
    act_start, act_end = action_pos_slice

    ee_positions = state[:, ee_start:ee_end]
    action_deltas = action[:, act_start:act_end]

    # Compute next_ee_positions within each episode
    next_ee = np.full_like(ee_positions, np.nan)
    for ep in np.unique(episodes):
        mask = episodes == ep
        indices = np.where(mask)[0]
        if len(indices) > 1:
            next_ee[indices[:-1]] = ee_positions[indices[1:]]

    return {
        "ee_positions": ee_positions,
        "actions": action_deltas,
        "episodes": episodes,
        "frames": frames,
        "next_ee_positions": next_ee,
    }
