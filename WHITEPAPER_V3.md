# Axiom: Feasible Transformation Space Mapping and Counterfactual Repair for Physical AI

**Version 0.3.0 — March 2026**

---

## Abstract

Physical AI systems — LLMs, VLAs, and learned policies — generate robot actions without knowing what is physically possible on a given robot in a given environment. The standard response is to discover failures at execution time and debug manually. We present Axiom, a system that validates robot actions against physical constraints, computes counterfactual fixes when they fail, and maps the feasible transformation space for a given robot. The system operates at three levels: (1) point-checking individual actions against a gate pipeline, (2) auditing trajectory datasets with cross-robot portability analysis, and (3) characterizing the full feasible space to quantify robot capability overlap and dataset coverage gaps.

We demonstrate the system on real data: LIBERO trajectories collected on a Franka evaluated against a UR3e. The atlas reveals that Franka covers 100% of UR3e's feasible space while UR3e covers only 7.2% of Franka's — quantifying exactly why cross-robot transfer fails. Dataset coverage analysis shows 43% of Franka-collected data is infeasible on UR3e, and the data covers only 26% of UR3e's feasible space, leaving 74% as blind spots.

The system is implemented as an open-source Python library (316 tests, MIT licensed) with CLI, REST API, and ROS 2 integration.

---

## 1. Introduction

### 1.1 The Problem

AI-generated robot actions fail on real hardware because the generating system does not know the physical constraints of the target robot. An LLM proposes coordinates beyond the arm's reach. A VLA trained on one robot produces trajectories infeasible on another. A policy trained in simulation hits joint limits or keepout zones when deployed.

These failures share a structure: the proposed transformation (move object from state A to state B) is **impossible** given the robot's physical constraints, and nobody checked before execution.

### 1.2 The Deeper Problem

Current validation approaches check individual points: "is this target reachable?" That answers one question but not the important ones:

- What is the **full set** of transformations this robot can perform?
- Where does this dataset **cover** the feasible space, and where are the gaps?
- How much of Robot A's capability **overlaps** with Robot B's?
- What is the **nearest feasible alternative** when a proposed action is impossible?

These are questions about the **feasible transformation space** — the boundary between possible and impossible actions for a given robot and environment. Characterizing this space, rather than checking individual points against it, is what enables systematic portability analysis, coverage assessment, and repair.

### 1.3 Contributions

Axiom provides three capabilities:

1. **Gate pipeline with counterfactual fixes.** Five deterministic physics gates (IK feasibility, reachability, payload, keepout, path keepout) that produce not just pass/fail verdicts but structured fixes — the smallest change that makes a failed action feasible, with exact coordinates and natural-language instructions.

2. **Trajectory audit with portability analysis.** Batch analysis of trajectory datasets against robot profiles, computing EE velocity/jerk, reach margins, and IK feasibility. With cross-robot portability mode, each violation is confirmed via IK and paired with a computed nearest-feasible alternative.

3. **Feasible space atlas.** 3D sampling of the feasible transformation space via IK at each grid point, producing capability maps, robot overlap reports, and dataset coverage overlays.

---

## 2. Architecture

### 2.1 Data Model

The system uses constructor-theoretic vocabulary, which maps directly to the physics:

- **Substrate** (`SubstrateSpec`): the object being transformed (ID, mass, initial pose)
- **Transformation** (`TransformationSpec`): the proposed change (target pose, tolerance, waypoints)
- **Constructor** (`ConstructorSpec`): the robot performing the transformation (ID, base pose, reach, payload, URDF)
- **Verdict**: `CAN` (transformation is possible), `CAN_WITH_PATCH` (possible after adjustment within tolerance), or `HARD_CANT` (impossible, no acceptable fix)

The verdict taxonomy is semantically honest. `CAN_WITH_PATCH` is distinct from `CAN` — the system explicitly reports what was changed, by how much, and whether the deviation risks breaking task intent.

### 2.2 Gate Pipeline

Gates execute in fixed order with short-circuit evaluation:

```
IK Feasibility (if URDF provided)
    |-- PASS -> skip Reachability (IK subsumes it)
    |-- FAIL -> stop, compute fix, return evidence
    |-- SKIP (no URDF) -> fall through
    v
Reachability (spherical fallback)
    |-- FAIL -> stop
    v
Payload
    |-- FAIL -> stop
    v
Keepout Zones (endpoint)
    |-- FAIL -> stop
    v
Path Keepout (if waypoints)
    |-- FAIL -> stop
    v
All passed -> CAN
```

### 2.3 Counterfactual Fix Engine

Every gate failure produces one or more **counterfactual fixes** — the minimal change that crosses the boundary from impossible to possible:

| Fix Type | Gates | Description |
|----------|-------|-------------|
| `MOVE_TARGET` | IK, Reach, Keepout | Move target to nearest feasible point |
| `MOVE_BASE` | Reach | Move robot base toward target |
| `SPLIT_PAYLOAD` | Payload | Divide into multiple trips |
| `CHANGE_CONSTRUCTOR` | IK, Reach, Payload | Suggest a more capable robot |

Each fix carries dual representation: a human-readable instruction (for LLMs) and a machine-readable patch with exact coordinates (for programmatic use).

### 2.4 Semantic Honesty

The `gate` command distinguishes three outcomes:

- **`CAN`** — target feasible as proposed
- **`CAN_WITH_PATCH`** — feasible only after adjustment within `--max-deviation` tolerance (default 50mm)
- **`HARD_CANT`** — infeasible, or patch exceeds tolerance

Every patch reports: `patch_delta_m`, `patch_delta_mm`, `intent_risk` (`LOW`/`HIGH`), `patched_fields`, and `accepted_target_xyz`. A 262mm patch at 50mm tolerance is honestly rejected as `HARD_CANT` with `intent_risk: HIGH` — the system does not claim the task is solved when the target moved 26cm.

---

## 3. Feasible Space Atlas

### 3.1 Motivation

Point-checking answers "is this one target feasible?" The atlas answers "what is the full set of feasible targets?" This is the difference between checking a single word against a dictionary and having the dictionary.

The atlas enables three analyses that point-checking cannot:

1. **Robot overlap** — quantify what fraction of Robot A's feasible space Robot B can also reach
2. **Dataset coverage** — quantify what fraction of the feasible space a training dataset occupies
3. **Deployment gap** — quantify how deployment constraints (keepout zones, base offset) reduce the feasible space

### 3.2 Method

The atlas samples a 3D grid of end-effector positions within the robot's reach envelope:

1. Generate grid points at configurable resolution (default 50mm)
2. Pre-filter: skip points outside 1.1x spherical reach (fast cull)
3. For remaining points, run inverse kinematics via ikpy
4. Verify each IK solution via forward kinematics (1cm position tolerance)
5. Record: feasible/infeasible, margin to boundary, IK position error

For robots with joint limits that place zero outside the feasible range (e.g. Franka joint 4: [-3.07, -0.07] rad), the solver uses midpoint initial guesses computed from the joint bounds.

### 3.3 Robot Overlap

Given two robots at the same base position, the atlas samples the union of their reach envelopes and classifies each point:

- **Both feasible** — in the overlap
- **Only Robot A** — A can reach, B cannot
- **Only Robot B** — B can reach, A cannot
- **Neither** — outside both workspaces

Overlap metrics: overlap percentage (Jaccard-style), A's coverage of B, B's coverage of A.

### 3.4 Dataset Coverage

Given an atlas and a set of EE positions from a trajectory dataset:

1. Voxelize the atlas at grid resolution
2. Map each data point to its nearest voxel
3. Count: data points in feasible voxels, data points in infeasible voxels
4. Count: feasible voxels with at least one data point (occupied)

Coverage metrics: space coverage (% of feasible voxels occupied), data feasibility (% of data in feasible space).

### 3.5 Outputs

The atlas produces three artifact files:

- `atlas_summary.json` — robot, resolution, bounds, feasible/infeasible counts
- `atlas_points.csv` — point cloud (x, y, z, feasible, margin_m) for downstream tools
- `atlas_points.jsonl` — detailed point cloud with IK error values

Overlap and coverage produce separate JSON reports.

---

## 4. Trajectory Audit

### 4.1 Audit Engine

The audit engine analyzes trajectory datasets (LeRobot HuggingFace format or JSONL) against a robot profile:

- **Reach margin**: Euclidean distance from each EE position to the robot base vs. max reach
- **EE velocity/jerk**: computed from consecutive EE positions at the dataset's control rate
- **Joint position/velocity limits**: validated against URDF-specified bounds (when joint data available)
- **IK feasibility**: optional IK check on worst-reach-margin steps
- **Keepout zone violations**: optional spatial constraint checking

### 4.2 Cross-Robot Portability

With `--port-from`, the audit runs data collected on one robot against a different robot's profile. Each reach violation is fed through the SDK's `check_simple()` for IK confirmation and counterfactual fix computation:

```bash
axiom audit lerobot/libero_10 --robot ur3e --base-z 0.91 --hz 10 -n 10 --port-from franka
```

Output: per-step patches with original XYZ, IK verdict, fix type, nearest feasible alternative, and patch displacement.

---

## 5. Results

### 5.1 Planner Loop

Four scenarios on a UR3e (base at z=0.91m, 50mm deviation tolerance):

| Scenario | Verdict | Detail |
|----------|---------|--------|
| Cup from table (0.3m reach) | `CAN` | Feasible as proposed |
| Cup on nearby counter (0.45m) | `CAN` | Feasible as proposed |
| Cup on far shelf (0.8m) | `HARD_CANT` | IK infeasible, nearest fix at 262mm — exceeds 50mm tolerance |
| 15kg box | `HARD_CANT` | Payload 15kg exceeds 3kg limit, no geometric fix |

The system correctly distinguishes between failures that have geometric fixes (but are too large to accept) and failures with no geometric fix at all (payload). Both are `HARD_CANT` but for different reasons, enabling the planner to choose different recovery strategies.

### 5.2 Robot Overlap: UR3e vs Franka

At base position [0, 0, 0.91], 150mm grid resolution:

| Metric | Value |
|--------|-------|
| Both feasible | 35 points |
| Only UR3e | 0 points |
| Only Franka | 454 points |
| Overlap | 7.2% |
| Franka covers UR3e | 100% |
| UR3e covers Franka | 7.2% |

Every point UR3e can reach, Franka can also reach. But 93% of Franka's feasible space is unreachable by UR3e. This quantifies the portability gap: transferring Franka data to UR3e will encounter infeasible regions.

### 5.3 Dataset Coverage: LIBERO on UR3e

3 episodes from `lerobot/libero_10`, 843 EE positions, 150mm grid:

| Metric | Value |
|--------|-------|
| UR3e feasible voxels | 42 |
| Voxels with data | 11 (26.2%) |
| Data in feasible space | 478 (56.7%) |
| Data in infeasible space | 365 (43.3%) |

43% of the Franka-collected data lands in regions the UR3e cannot reach. Even the feasible data only covers 26% of what the UR3e can do — 74% of the feasible space has no training data at all.

### 5.4 Portability Audit: LIBERO on UR3e

10 episodes from `lerobot/libero_10`, 2,758 steps:

| Metric | Value |
|--------|-------|
| Steps with reach violations | 23 (0.8%) |
| Episodes affected | 2/10 (20%) |
| IK-confirmed infeasible | 23/23 (100%) |
| Mean patch displacement | 46.5mm |
| Max patch displacement | 55.0mm |

Every reach violation was confirmed infeasible by IK. Each violation has a computed nearest-feasible alternative with measured displacement.

---

## 6. Supported Robots

| Robot | DOF | Reach (m) | Payload (kg) | Joint Limits | Vel Limits |
|-------|-----|-----------|-------------|:---:|:---:|
| UR3e | 6 | 0.50 | 3.0 | - | - |
| UR5e | 6 | 1.85 | 5.0 | - | - |
| UR10e | 6 | 1.30 | 12.5 | - | - |
| Franka | 7 | 0.855 | 3.0 | Full | Full |
| KUKA iiwa14 | 7 | 0.82 | 14.0 | - | - |

All robots ship with bundled URDFs. Franka has full joint position limits, velocity limits, and max EE speed specified.

---

## 7. Integration

### 7.1 CLI

```bash
# Feasibility gate
axiom gate 0.8,0.3,1.11 --robot ur3e --base-z 0.91
axiom gate 0.8,0.3,1.11 --robot ur3e --auto-fix --max-deviation 0.05 --json

# Feasible space atlas
axiom atlas ur3e --base-z 0.91 --resolution 0.05
axiom atlas ur3e --compare franka --base-z 0.91
axiom atlas ur3e --dataset lerobot/libero_10 --base-z 0.91 -n 5

# Trajectory audit
axiom audit lerobot/libero_10 --robot franka --base-z 0.91 --hz 10 -n 10
axiom audit lerobot/libero_10 --robot ur3e --base-z 0.91 --hz 10 --port-from franka

# YAML-based checks
axiom run task.yaml --junit
axiom sweep task.yaml --n 50 --seed 1337
axiom replay regressions/ --out artifacts/
```

All commands exit 0 on pass, 2 on fail. JUnit XML output for CI.

### 7.2 Python SDK

```python
from axiom_tfg import check_simple, validate_action, validate_plan
from axiom_tfg.atlas import sample_feasible_space, compute_overlap, compute_coverage
from axiom_tfg.audit import audit_trajectory, load_lerobot_trajectory, AuditConfig
```

### 7.3 REST API and ROS 2

REST API (`POST /runs`, `/sweeps`, `/ai/generate`) and ROS 2 Nav2 pre-flight proxy are available for online integration.

---

## 8. Limitations

**Position-only atlas.** The atlas samples EE position but not orientation. Real tasks care about gripper orientation, which further constrains the feasible space.

**IK solver speed.** ikpy is pure Python. Atlas at 50mm resolution on a 7-DOF arm takes minutes. Faster solvers or precomputed lookup tables would enable finer resolution.

**Static constraints only.** Gates check kinematic feasibility, not dynamics (velocity/torque along the path) or contact physics (grasp feasibility, friction).

**AABB keepout zones.** Forbidden regions are axis-aligned boxes. Real environments have complex geometry.

**Patches are not task-aware.** A counterfactual fix computes the nearest feasible point, not the nearest point that still achieves the task goal. A "place on shelf" action moved 262mm closer is reachable but may miss the shelf. The system reports this honestly via `intent_risk` and `patch_delta_m`, but the planner must decide whether the fix preserves intent.

---

## 9. Future Directions

**Orientation-aware atlas.** Sample orientation as well as position to characterize the full 6D feasible space.

**Faster solvers.** Replace ikpy with analytical IK or compiled solvers (e.g. TracIK, pink) for real-time atlas computation.

**Coverage-guided data collection.** Use atlas gaps to guide where to collect new training data — fill the blind spots identified by coverage analysis.

**Multi-robot overlap database.** Pre-compute overlap matrices across all supported robots to enable instant portability decisions.

**Trajectory-level repair.** Extend from point fixes to full trajectory replanning that respects both physical constraints and task semantics.

---

## 10. Conclusion

Axiom provides three capabilities for physical AI development: deterministic feasibility validation with counterfactual fixes, trajectory dataset auditing with cross-robot portability analysis, and feasible transformation space mapping with robot overlap and dataset coverage reports.

The system produces concrete, actionable artifacts: structured verdicts with deviation reporting, point clouds of feasible space, quantified overlap between robots, and measured coverage gaps in datasets. These artifacts are designed to be consumed by planners, CI pipelines, and data collection workflows — not by humans interpreting log files.

316 tests. Five robots. MIT licensed. `pip install -e ".[dev]"`.

---

## References

[1] Liang, J. et al. "Code as Policies." arXiv:2209.07753, 2022.
[2] Vemprala, S. et al. "ChatGPT for Robotics." Microsoft Research, 2023.
[3] Singh, I. et al. "ProgPrompt." arXiv:2209.11302, 2022.
[4] NVIDIA. "GR00T N1." NVIDIA, 2025.
[5] Brohan, A. et al. "RT-2." arXiv:2307.15818, 2023.
[6] "Vision-Language-Action Models." arXiv:2505.04769, 2025.
[7] Lin, K. et al. "Text2Motion." arXiv:2303.12153, 2023.
[8] Ahn, M. et al. "SayCan." arXiv:2204.01691, 2022.
[9] Kim, J. et al. "Modular Safety Guardrails." arXiv:2602.04056, 2026.
[10] ISO 10218-1:2025. Robots and Robotic Devices — Safety Requirements.
