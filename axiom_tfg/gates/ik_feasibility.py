"""IK feasibility gate: does an inverse-kinematics solution exist for the target pose?

When the TaskSpec constructor provides ``urdf_path``, this gate loads the URDF
via *ikpy*, computes IK for the target pose (position-only or full 6-DOF when
orientation is specified), and verifies the solution is within tolerance.

Multi-start: to reduce false negatives from local-optimiser traps, the gate
runs K deterministic initial seeds and keeps the best result.

If ``urdf_path`` is not set the gate is skipped (the simpler spherical
reachability gate still runs).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from axiom_tfg.models import (
    CounterfactualFix,
    FixType,
    GateResult,
    GateStatus,
    TaskSpec,
)

GATE_NAME = "ik_feasibility"

# Default thresholds
_IK_POSITION_TOL_M = 0.01          # 1 cm
_IK_ORIENTATION_TOL_RAD = 0.1745   # ~10 degrees
_MULTI_START_K = 6                  # number of deterministic seeds


# ── Quaternion / rotation helpers ─────────────────────────────────────────


def _quat_to_rotation_matrix(w: float, x: float, y: float, z: float) -> list[list[float]]:
    """Convert unit quaternion (w, x, y, z) to a 3x3 rotation matrix (nested list)."""
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _angular_distance(r_target: Any, r_actual: Any) -> float:
    """Compute angular distance (radians) between two 3x3 rotation matrices (numpy)."""
    import numpy as np

    r_diff = np.asarray(r_target).T @ np.asarray(r_actual)
    trace = float(np.trace(r_diff))
    # Clamp for numerical safety.
    cos_angle = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    return math.acos(cos_angle)


# ── Chain loader ──────────────────────────────────────────────────────────


def _load_chain(urdf_path: str, base_link: str | None, ee_link: str | None):
    """Load an ikpy Chain from a URDF file."""
    import warnings

    import ikpy.chain

    kwargs: dict[str, Any] = {}
    if base_link:
        kwargs["base_elements"] = [base_link]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        chain = ikpy.chain.Chain.from_urdf_file(str(urdf_path), **kwargs)

    # Fixed links inactive, revolute/prismatic joints active.
    chain.active_links_mask = [link.joint_type != "fixed" for link in chain.links]
    return chain


# ── Multi-start seed generation ───────────────────────────────────────────


def _generate_seeds(chain, k: int = _MULTI_START_K) -> list:
    """Generate K deterministic initial joint configurations.

    Seeds are spaced across the joint range so the local optimiser starts
    from different basins.  Seed 0 is the midpoint of each joint's range
    (clamped so it's always valid even for joints that don't span zero).
    """
    import numpy as np

    n = len(chain.links)

    # Seed 0: midpoint of each joint range (safe for asymmetric bounds).
    q0 = np.zeros(n)
    for j, (link, active) in enumerate(
        zip(chain.links, chain.active_links_mask)
    ):
        if not active:
            continue
        lo, hi = link.bounds
        if math.isinf(lo) or math.isinf(hi):
            lo, hi = -math.pi, math.pi
        q0[j] = (lo + hi) / 2
    seeds: list = [q0]

    for i in range(1, k):
        q = np.zeros(n)
        fraction = i / k
        for j, (link, active) in enumerate(
            zip(chain.links, chain.active_links_mask)
        ):
            if not active:
                continue
            lo, hi = link.bounds
            if math.isinf(lo) or math.isinf(hi):
                lo, hi = -math.pi, math.pi
            q[j] = lo + (hi - lo) * fraction
        seeds.append(q)

    return seeds


# ── Core gate logic ───────────────────────────────────────────────────────


def check_ik_feasibility(
    spec: TaskSpec,
) -> tuple[GateResult, list[CounterfactualFix]] | None:
    """Check whether an IK solution exists for the target pose.

    Returns ``None`` when the constructor has no ``urdf_path`` (gate skipped).
    Otherwise returns ``(GateResult, fixes)``.
    """
    if not spec.constructor.urdf_path:
        return None  # skip — no URDF provided

    urdf = Path(spec.constructor.urdf_path)
    if not urdf.is_file():
        return (
            GateResult(
                gate_name=GATE_NAME,
                status=GateStatus.FAIL,
                measured_values={"error": f"URDF not found: {urdf}"},
                reason_code="URDF_NOT_FOUND",
            ),
            [],
        )

    import numpy as np

    chain = _load_chain(
        str(urdf),
        spec.constructor.base_link,
        spec.constructor.ee_link,
    )

    # Target position in the constructor's base frame.
    base = spec.constructor.base_pose.xyz
    target_world = spec.transformation.target_pose.xyz
    target_pos = [t - b for t, b in zip(target_world, base)]

    # Orientation target (optional).
    quat = spec.transformation.target_quat_wxyz
    has_orientation = quat is not None
    target_rot_np = None
    if has_orientation:
        target_rot = _quat_to_rotation_matrix(*quat)
        target_rot_np = np.array(target_rot)

    # Tolerance thresholds.
    pos_tol = spec.transformation.tolerance_m or _IK_POSITION_TOL_M
    ori_tol = spec.transformation.orientation_tolerance_rad or _IK_ORIENTATION_TOL_RAD

    # ── Multi-start IK ────────────────────────────────────────────────
    seeds = _generate_seeds(chain, _MULTI_START_K)

    best_pos_err = math.inf
    best_ori_err = math.inf
    best_combined = math.inf
    best_angles = None
    best_fk = None

    for seed in seeds:
        if has_orientation:
            ik_angles = chain.inverse_kinematics(
                target_position=target_pos,
                target_orientation=target_rot_np,
                orientation_mode="all",
                initial_position=seed,
            )
        else:
            ik_angles = chain.inverse_kinematics(
                target_position=target_pos,
                initial_position=seed,
            )

        fk_matrix = chain.forward_kinematics(ik_angles)
        fk_pos = fk_matrix[:3, 3]

        pos_err = float(np.linalg.norm(fk_pos - np.array(target_pos)))
        ori_err = 0.0
        if has_orientation:
            ori_err = _angular_distance(target_rot_np, fk_matrix[:3, :3])

        # Score: weighted sum (position in metres, orientation in radians).
        combined = pos_err + ori_err
        if combined < best_combined:
            best_combined = combined
            best_pos_err = pos_err
            best_ori_err = ori_err
            best_angles = ik_angles
            best_fk = fk_matrix

    # ── Evaluate result ───────────────────────────────────────────────
    pos_ok = best_pos_err <= pos_tol
    ori_ok = (not has_orientation) or (best_ori_err <= ori_tol)
    solved = pos_ok and ori_ok

    # Build joint-solution dict.
    joint_names = [link.name for link in chain.links]
    active_joints = {
        name: round(float(angle), 6)
        for name, angle, active in zip(
            joint_names, best_angles, chain.active_links_mask
        )
        if active
    }

    fk_pos_list = best_fk[:3, 3].tolist()

    measured: dict[str, Any] = {
        "solver": "ikpy",
        "ik_success": solved,
        "attempts": _MULTI_START_K,
        "best_position_error_m": round(best_pos_err, 6),
        "target_xyz": target_pos,
        "fk_result_xyz": [round(v, 6) for v in fk_pos_list],
    }

    if has_orientation:
        measured["target_quat_wxyz"] = quat
        measured["best_orientation_error_rad"] = round(best_ori_err, 6)
        measured["best_orientation_error_deg"] = round(math.degrees(best_ori_err), 2)
        # FK orientation as quaternion.
        measured["fk_quat_wxyz"] = _rotation_matrix_to_quat(best_fk[:3, :3])
        measured["position_tolerance_m"] = pos_tol
        measured["orientation_tolerance_rad"] = ori_tol

    if solved:
        measured["joint_solution"] = active_joints

    if solved:
        return (
            GateResult(
                gate_name=GATE_NAME,
                status=GateStatus.PASS,
                measured_values=measured,
            ),
            [],
        )

    # ── FAIL path ─────────────────────────────────────────────────────
    # Determine reason code.
    if pos_ok and not ori_ok:
        reason_code = "ORIENTATION_MISMATCH"
    else:
        reason_code = "NO_IK_SOLUTION"

    fixes: list[CounterfactualFix] = []
    adj = spec.allowed_adjustments

    if adj.can_move_target:
        reachable = [round(f + b, 6) for f, b in zip(fk_pos_list, base)]
        instruction_parts = []
        if not pos_ok:
            instruction_parts.append(
                f"position error {best_pos_err:.4f} m exceeds {pos_tol} m tolerance"
            )
        if has_orientation and not ori_ok:
            instruction_parts.append(
                f"orientation error {math.degrees(best_ori_err):.1f}° exceeds "
                f"{math.degrees(ori_tol):.1f}° tolerance"
            )
        fixes.append(
            CounterfactualFix(
                type=FixType.MOVE_TARGET,
                delta=round(best_pos_err, 6),
                instruction=(
                    f"No IK solution: {'; '.join(instruction_parts)}. "
                    f"Nearest reachable point: {reachable}."
                ),
                proposed_patch={"projected_target_xyz": reachable},
            )
        )

    if adj.can_change_constructor:
        fixes.append(
            CounterfactualFix(
                type=FixType.CHANGE_CONSTRUCTOR,
                delta=round(best_pos_err, 6),
                instruction=(
                    f"No IK solution for target with {spec.constructor.id}. "
                    f"Consider a robot with longer reach or different kinematics."
                ),
            )
        )

    return (
        GateResult(
            gate_name=GATE_NAME,
            status=GateStatus.FAIL,
            measured_values=measured,
            reason_code=reason_code,
        ),
        fixes,
    )


def _rotation_matrix_to_quat(r) -> list[float]:
    """Convert a 3x3 rotation matrix to quaternion [w, x, y, z]."""
    import numpy as np

    r = np.asarray(r)
    trace = float(np.trace(r))
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (r[2, 1] - r[1, 2]) * s
        y = (r[0, 2] - r[2, 0]) * s
        z = (r[1, 0] - r[0, 1]) * s
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = 2.0 * math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2])
        w = (r[2, 1] - r[1, 2]) / s
        x = 0.25 * s
        y = (r[0, 1] + r[1, 0]) / s
        z = (r[0, 2] + r[2, 0]) / s
    elif r[1, 1] > r[2, 2]:
        s = 2.0 * math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2])
        w = (r[0, 2] - r[2, 0]) / s
        x = (r[0, 1] + r[1, 0]) / s
        y = 0.25 * s
        z = (r[1, 2] + r[2, 1]) / s
    else:
        s = 2.0 * math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1])
        w = (r[1, 0] - r[0, 1]) / s
        x = (r[0, 2] + r[2, 0]) / s
        y = (r[1, 2] + r[2, 1]) / s
        z = 0.25 * s
    return [round(v, 6) for v in [w, x, y, z]]
