"""Tests for the physical margin audit engine."""

import json
from pathlib import Path

import numpy as np
import pytest

from axiom_tfg.audit import (
    AuditConfig,
    AuditReport,
    KeepoutSpec,
    StepEvidence,
    FlaggedMoment,
    audit_trajectory,
    write_audit_report,
    _keepout_margin,
    _compute_ee_velocities,
    _audit_joint_dynamics,
)
from axiom_tfg.robots import ROBOT_REGISTRY


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def franka_config():
    """Basic Franka config with base at origin."""
    return AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0])


@pytest.fixture
def franka_config_libero():
    """Franka config with LIBERO-typical base height."""
    return AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.91])


@pytest.fixture
def config_with_keepout():
    """Config with a keepout zone."""
    return AuditConfig(
        robot="franka",
        base_xyz=[0.0, 0.0, 0.0],
        keepout_zones=[
            KeepoutSpec(
                id="safety_cage",
                min_xyz=[0.3, 0.3, 0.0],
                max_xyz=[0.6, 0.6, 0.5],
                safety_buffer=0.0,
            )
        ],
    )


# ── Keepout margin ───────────────────────────────────────────────────────


class TestKeepoutMargin:
    def test_point_outside_zone(self):
        zone = KeepoutSpec(id="z1", min_xyz=[0.0, 0.0, 0.0], max_xyz=[1.0, 1.0, 1.0], safety_buffer=0.0)
        point = np.array([2.0, 0.5, 0.5])
        margin = _keepout_margin(point, zone)
        assert margin > 0
        assert abs(margin - 1.0) < 1e-6

    def test_point_inside_zone(self):
        zone = KeepoutSpec(id="z1", min_xyz=[0.0, 0.0, 0.0], max_xyz=[1.0, 1.0, 1.0], safety_buffer=0.0)
        point = np.array([0.5, 0.5, 0.5])
        margin = _keepout_margin(point, zone)
        assert margin < 0
        assert abs(margin - (-0.5)) < 1e-6

    def test_point_on_boundary(self):
        zone = KeepoutSpec(id="z1", min_xyz=[0.0, 0.0, 0.0], max_xyz=[1.0, 1.0, 1.0], safety_buffer=0.0)
        point = np.array([0.0, 0.5, 0.5])
        margin = _keepout_margin(point, zone)
        assert margin <= 0

    def test_safety_buffer_expands_zone(self):
        zone = KeepoutSpec(id="z1", min_xyz=[0.0, 0.0, 0.0], max_xyz=[1.0, 1.0, 1.0], safety_buffer=0.1)
        point = np.array([-0.05, 0.5, 0.5])
        margin = _keepout_margin(point, zone)
        assert margin < 0


# ── Basic audit ──────────────────────────────────────────────────────────


class TestAuditTrajectory:
    def test_all_within_reach(self, franka_config):
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.4, 0.1, 0.0],
            [0.2, 0.2, 0.1],
        ])
        report = audit_trajectory(ee, franka_config)
        assert report.total_steps == 3
        assert report.reach_violations == 0
        assert len([f for f in report.flagged if "REACH" in f.flag_type]) == 0

    def test_beyond_reach_flagged(self, franka_config):
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.9, 0.0, 0.0],
        ])
        report = audit_trajectory(ee, franka_config)
        assert report.reach_violations == 1
        violations = [f for f in report.flagged if f.flag_type == "REACH_VIOLATION"]
        assert len(violations) == 1
        assert violations[0].step == 1

    def test_reach_warning(self, franka_config):
        dist = 0.855 * 0.92
        ee = np.array([[dist, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        assert report.reach_violations == 0
        warnings = [f for f in report.flagged if f.flag_type == "REACH_WARNING"]
        assert len(warnings) == 1

    def test_reach_critical(self, franka_config):
        dist = 0.855 * 0.97
        ee = np.array([[dist, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        assert report.reach_violations == 0
        critical = [f for f in report.flagged if f.flag_type == "REACH_CRITICAL"]
        assert len(critical) == 1

    def test_keepout_violation(self, config_with_keepout):
        ee = np.array([
            [0.1, 0.1, 0.1],
            [0.4, 0.4, 0.2],
        ])
        report = audit_trajectory(ee, config_with_keepout)
        assert report.keepout_violations == 1
        assert report.keepout_violations_by_zone["safety_cage"] == 1
        kv = [f for f in report.flagged if f.flag_type == "KEEPOUT_VIOLATION"]
        assert len(kv) == 1
        assert kv[0].step == 1

    def test_keepout_safe(self, config_with_keepout):
        ee = np.array([
            [0.1, 0.1, 0.1],
            [0.8, 0.8, 0.8],
        ])
        report = audit_trajectory(ee, config_with_keepout)
        assert report.keepout_violations == 0

    def test_episode_tracking(self, franka_config):
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.3, 0.1, 0.0],
            [0.3, 0.0, 0.0],
            [0.3, 0.1, 0.0],
        ])
        episodes = np.array([0, 0, 1, 1])
        report = audit_trajectory(ee, franka_config, episodes=episodes)
        assert report.total_episodes == 2
        assert report.total_steps == 4

    def test_libero_base_offset(self, franka_config_libero):
        ee = np.array([[0.0, 0.0, 0.5]])
        report = audit_trajectory(ee, franka_config_libero)
        assert report.reach_violations == 0
        assert report.steps_evidence[0].dist_from_base == pytest.approx(0.41, abs=0.01)


# ── EE velocity analysis ────────────────────────────────────────────────


class TestEEVelocity:
    def test_stationary_trajectory(self):
        """No movement → zero speed."""
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.3, 0.0, 0.0],
            [0.3, 0.0, 0.0],
        ])
        episodes = np.array([0, 0, 0])
        speeds, jerks = _compute_ee_velocities(ee, episodes, dt=0.05)
        assert speeds[0] == pytest.approx(0.0)
        assert speeds[1] == pytest.approx(0.0)
        assert np.isnan(speeds[2])  # last step has no next

    def test_constant_velocity(self):
        """Uniform motion → constant speed, zero jerk."""
        # Moving 0.01m per step at 20Hz = 0.2 m/s
        ee = np.array([
            [0.30, 0.0, 0.0],
            [0.31, 0.0, 0.0],
            [0.32, 0.0, 0.0],
            [0.33, 0.0, 0.0],
            [0.34, 0.0, 0.0],
        ])
        episodes = np.array([0, 0, 0, 0, 0])
        speeds, jerks = _compute_ee_velocities(ee, episodes, dt=0.05)
        assert speeds[0] == pytest.approx(0.2, abs=0.001)
        assert speeds[1] == pytest.approx(0.2, abs=0.001)
        # Constant velocity → zero acceleration → zero jerk
        valid_jerks = jerks[~np.isnan(jerks)]
        for j in valid_jerks:
            assert j == pytest.approx(0.0, abs=0.01)

    def test_episode_boundary_no_bleed(self):
        """Speed at episode boundaries should be NaN, not computed across episodes."""
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.4, 0.0, 0.0],  # end ep0
            [0.0, 0.0, 0.0],  # start ep1 (discontinuity)
            [0.1, 0.0, 0.0],
        ])
        episodes = np.array([0, 0, 1, 1])
        speeds, _ = _compute_ee_velocities(ee, episodes, dt=0.05)
        # step 0 → step 1 within ep0: speed = 0.1/0.05 = 2.0
        assert speeds[0] == pytest.approx(2.0)
        # step 1 is last in ep0 → NaN
        assert np.isnan(speeds[1])
        # step 2 → step 3 within ep1: speed = 0.1/0.05 = 2.0
        assert speeds[2] == pytest.approx(2.0)
        assert np.isnan(speeds[3])

    def test_speed_violation_flagged(self):
        """EE speed exceeding robot max should be flagged."""
        # Franka max_ee_speed = 1.7 m/s
        # Moving 0.1m per step at 20Hz = 2.0 m/s (exceeds 1.7)
        config = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0], control_hz=20.0)
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.4, 0.0, 0.0],
        ])
        report = audit_trajectory(ee, config)
        assert report.ee_speed_max == pytest.approx(2.0)
        assert report.ee_speed_violations == 1
        violations = [f for f in report.flagged if f.flag_type == "EE_SPEED_VIOLATION"]
        assert len(violations) == 1

    def test_speed_within_limit(self):
        """EE speed below robot max should not be flagged."""
        config = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0], control_hz=20.0)
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.305, 0.0, 0.0],  # 0.005m per step @ 20Hz = 0.1 m/s
        ])
        report = audit_trajectory(ee, config)
        assert report.ee_speed_max == pytest.approx(0.1)
        assert report.ee_speed_violations == 0

    def test_high_jerk_computed(self):
        """Sudden acceleration change produces nonzero jerk."""
        # Accelerating then decelerating: vel goes [0.1, 0.2, 0.1] → accel changes
        config = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0], control_hz=20.0)
        dt = 0.05  # 20Hz
        ee = np.array([
            [0.300, 0.0, 0.0],
            [0.305, 0.0, 0.0],  # vel = 0.1 m/s
            [0.315, 0.0, 0.0],  # vel = 0.2 m/s
            [0.330, 0.0, 0.0],  # vel = 0.3 m/s
            [0.340, 0.0, 0.0],  # vel = 0.2 m/s (deceleration)
            [0.345, 0.0, 0.0],  # vel = 0.1 m/s
        ])
        report = audit_trajectory(ee, config)
        assert report.ee_jerk_max > 0
        assert report.ee_speed_max > 0

    def test_control_hz_affects_speed(self):
        """Different control rates produce different speed values."""
        ee = np.array([[0.3, 0.0, 0.0], [0.31, 0.0, 0.0]])

        config_20hz = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0], control_hz=20.0)
        config_50hz = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0], control_hz=50.0)

        report_20 = audit_trajectory(ee, config_20hz)
        report_50 = audit_trajectory(ee, config_50hz)

        # 0.01m at 20Hz = 0.2 m/s; at 50Hz = 0.5 m/s
        assert report_20.ee_speed_max == pytest.approx(0.2, abs=0.001)
        assert report_50.ee_speed_max == pytest.approx(0.5, abs=0.001)


# ── Joint dynamics ───────────────────────────────────────────────────────


class TestJointDynamics:
    def test_joint_velocity_within_limits(self):
        """Joint velocities within limits produce no violations."""
        profile = ROBOT_REGISTRY["franka"]
        dt = 0.05  # 20Hz
        # Small joint movements: 0.01 rad/step → 0.2 rad/s (well below 2.175 limit)
        q = np.array([
            [0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            [0.01, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            [0.02, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
        ])
        episodes = np.array([0, 0, 0])
        result = _audit_joint_dynamics(q, episodes, profile, dt)
        assert result["vel_violations"] == 0

    def test_joint_velocity_exceeds_limit(self):
        """Joint velocity exceeding limit is flagged."""
        profile = ROBOT_REGISTRY["franka"]
        dt = 0.05  # 20Hz
        # Joint 0: 0.2 rad/step → 4.0 rad/s (exceeds 2.175 limit)
        q = np.array([
            [0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            [0.2, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            [0.4, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
        ])
        episodes = np.array([0, 0, 0])
        result = _audit_joint_dynamics(q, episodes, profile, dt)
        assert result["vel_violations"] >= 1
        assert result["vel_violations_by_joint"][0] >= 1

    def test_joint_position_violation(self):
        """Joint position outside limits is flagged."""
        profile = ROBOT_REGISTRY["franka"]
        dt = 0.05
        # Joint 1 limit is [-1.7628, 1.7628]; put it at 2.0
        q = np.array([
            [0.0, 2.0, 0.0, -1.0, 0.0, 1.0, 0.0],
        ])
        episodes = np.array([0])
        result = _audit_joint_dynamics(q, episodes, profile, dt)
        assert result["pos_violations"] == 1

    def test_joint_data_in_audit(self):
        """Joint data flows through to the full audit report."""
        config = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0], control_hz=20.0)
        ee = np.array([
            [0.3, 0.0, 0.0],
            [0.3, 0.0, 0.0],
            [0.3, 0.0, 0.0],
        ])
        # Joint velocity violation on joint 0
        q = np.array([
            [0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            [0.2, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            [0.4, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
        ])
        report = audit_trajectory(ee, config, joint_positions=q)
        assert report.joint_vel_violations >= 1
        jv_flags = [f for f in report.flagged if f.flag_type == "JOINT_VEL_VIOLATION"]
        assert len(jv_flags) >= 1

    def test_episode_boundary_no_cross_velocity(self):
        """Joint velocity should not be computed across episode boundaries."""
        profile = ROBOT_REGISTRY["franka"]
        dt = 0.05
        q = np.array([
            [0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
            [0.01, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],  # end ep0
            [2.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],   # start ep1 (big jump)
            [2.01, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0],
        ])
        episodes = np.array([0, 0, 1, 1])
        result = _audit_joint_dynamics(q, episodes, profile, dt)
        # Should NOT see a huge velocity from the 0.01→2.0 jump
        assert result["vel_violations"] == 0


# ── IK feasibility ──────────────────────────────────────────────────────


class TestIKFeasibility:
    def test_ik_not_run_by_default(self, franka_config):
        ee = np.array([[0.3, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        assert report.ik_checks_performed == 0

    def test_ik_run_when_requested(self, franka_config):
        ee = np.array([[0.3, 0.0, 0.3]])
        report = audit_trajectory(ee, franka_config, run_ik=True)
        assert report.ik_checks_performed > 0
        assert report.steps_evidence[0].ik_feasible is not None

    def test_ik_infeasible_flagged(self):
        """A point beyond reach should be IK-infeasible."""
        config = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0])
        ee = np.array([[1.5, 0.0, 0.0]])  # way beyond reach
        report = audit_trajectory(ee, config, run_ik=True)
        assert report.ik_infeasible >= 1
        ik_flags = [f for f in report.flagged if f.flag_type == "IK_INFEASIBLE"]
        assert len(ik_flags) >= 1


# ── Output artifacts ─────────────────────────────────────────────────────


class TestAuditOutput:
    def test_summary_string(self, franka_config):
        ee = np.array([[0.3, 0.0, 0.0], [0.9, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        summary = report.summary()
        assert "franka" in summary
        assert "Reach Analysis" in summary
        assert "Violations" in summary

    def test_summary_includes_velocity(self):
        config = AuditConfig(robot="franka", base_xyz=[0.0, 0.0, 0.0])
        ee = np.array([[0.3, 0.0, 0.0], [0.31, 0.0, 0.0], [0.32, 0.0, 0.0]])
        report = audit_trajectory(ee, config)
        summary = report.summary()
        assert "EE Velocity" in summary
        assert "m/s" in summary

    def test_to_dict(self, franka_config):
        ee = np.array([[0.3, 0.0, 0.0], [0.9, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        d = report.to_dict()
        assert d["config"]["robot"] == "franka"
        assert d["summary"]["reach"]["violations"] == 1
        assert "ee_velocity" in d["summary"]
        assert "ik_feasibility" in d["summary"]
        assert "joint_dynamics" in d["summary"]
        assert len(d["flagged_moments"]) >= 1
        json.dumps(d)

    def test_to_dict_v2(self, franka_config):
        """v0.2 format includes new fields."""
        ee = np.array([[0.3, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        d = report.to_dict()
        assert d["audit_version"] == "0.2.0"
        assert "dof" in d["config"]
        assert "control_hz" in d["config"]
        assert "max_ee_speed_m_s" in d["config"]

    def test_write_artifacts(self, franka_config, tmp_path):
        ee = np.array([[0.3, 0.0, 0.0], [0.9, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        report_path = write_audit_report(report, tmp_path / "audit_out")

        assert report_path.exists()
        assert (tmp_path / "audit_out" / "flagged_moments.jsonl").exists()
        assert (tmp_path / "audit_out" / "audit_summary.txt").exists()

        data = json.loads(report_path.read_text())
        assert data["summary"]["reach"]["violations"] == 1

    def test_no_flagged_no_jsonl(self, franka_config, tmp_path):
        ee = np.array([[0.3, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        write_audit_report(report, tmp_path / "clean_audit")

        assert (tmp_path / "clean_audit" / "audit_report.json").exists()
        assert not (tmp_path / "clean_audit" / "flagged_moments.jsonl").exists()


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_trajectory(self, franka_config):
        ee = np.zeros((0, 3))
        report = audit_trajectory(ee, franka_config)
        assert report.total_steps == 0
        assert len(report.flagged) == 0

    def test_single_step(self, franka_config):
        ee = np.array([[0.3, 0.0, 0.0]])
        report = audit_trajectory(ee, franka_config)
        assert report.total_steps == 1
        assert report.ee_speed_max == 0.0  # single step, no velocity

    def test_multiple_keepout_zones(self):
        config = AuditConfig(
            robot="franka",
            base_xyz=[0.0, 0.0, 0.0],
            keepout_zones=[
                KeepoutSpec(id="z1", min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.1, 0.1, 0.1], safety_buffer=0.0),
                KeepoutSpec(id="z2", min_xyz=[0.2, 0.2, 0.0], max_xyz=[0.3, 0.3, 0.1], safety_buffer=0.0),
            ],
        )
        ee = np.array([
            [0.05, 0.05, 0.05],
            [0.25, 0.25, 0.05],
            [0.5, 0.5, 0.5],
        ])
        report = audit_trajectory(ee, config)
        assert report.keepout_violations == 2
        assert report.keepout_violations_by_zone["z1"] == 1
        assert report.keepout_violations_by_zone["z2"] == 1

    def test_flagged_sorted_by_severity(self, franka_config):
        ee = np.array([
            [0.84, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.83, 0.0, 0.0],
        ])
        report = audit_trajectory(ee, franka_config)
        assert report.flagged[0].margin < report.flagged[-1].margin
