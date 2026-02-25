# Axiom-TFG: A Physics Grounding Layer for LLM-Driven Robot Programming

**Version 0.1.0 — February 2026**

---

## Abstract

Large Language Models can generate robot action plans from natural language, but they lack knowledge of physical constraints — workspace limits, payload capacity, kinematic feasibility, and forbidden regions. This gap causes generated plans to fail on real hardware. We present Axiom-TFG, a deterministic physics grounding layer that sits between any action source (LLM, VLA, planner) and robot execution. Axiom validates proposed actions against a pipeline of physical feasibility gates and, critically, produces structured counterfactual fixes that feed back into the action source for automatic re-planning. The result is a closed-loop system where a non-expert can describe a task in plain English and receive a physically valid robot action sequence — without understanding kinematics, payload limits, or workspace geometry. The system is implemented as a Python library (236 tests, MIT licensed) with bindings for CLI, REST API, and ROS 2.

---

## 1. Introduction

### 1.1 The Problem

Robot programming today requires specialized expertise. Defining a pick-and-place task means understanding the robot's kinematic chain, workspace envelope, payload capacity, and environmental constraints. This knowledge barrier limits robotics adoption — small manufacturers, research labs, and logistics operations often have robots they cannot fully utilise because programming them requires scarce, expensive expertise.

LLMs offer a promising path: describe a task in natural language, have the model generate a robot program. Systems like Code as Policies (Google, 2023), ProgPrompt (Microsoft, 2023), and ChatGPT for Robotics (Microsoft, 2023) demonstrate that LLMs can produce syntactically valid robot code from language instructions. However, these systems share a critical weakness: **the LLM does not know what is physically possible for a specific robot in a specific environment.** It generates coordinates that are out of reach, masses that exceed payload limits, and paths that enter forbidden zones. These failures are discovered only at execution time — on real hardware or in expensive simulation.

### 1.2 The Gap

No existing system provides all of the following:

1. **Deterministic validation** — given a proposed action, compute a definitive pass/fail verdict based on physical constraints (not learned heuristics)
2. **Structured fixes** — when an action fails, produce not just an error message but an actionable correction with exact coordinates
3. **Closed-loop feedback** — feed the fix back to the action source automatically, enabling re-planning without human intervention
4. **Language-native constraints** — express fixes in natural language that an LLM can consume directly, closing the loop between physics and language

MoveIt provides motion planning but not fix generation. PyBullet provides simulation but not structured feedback. SayCan grounds in affordances but uses learned heuristics, not deterministic physics. None of these systems produce counterfactual fixes in a format an LLM can consume for re-planning.

### 1.3 Contribution

Axiom-TFG provides:

- A **deterministic gate pipeline** (IK feasibility, reachability, payload, keepout zones) that validates robot actions against physical constraints
- A **counterfactual fix engine** that computes the minimal change needed to make a failed action feasible, with both human-readable instructions and structured coordinate patches
- A **closed-loop resolver** that orchestrates propose → validate → fix → re-plan cycles between any action source and the gate pipeline
- An **LLM codegen adapter** that connects any OpenAI-compatible API to the resolver, enabling natural language to validated robot actions in a single function call

The system is pip-installable, requires no simulation environment, and works with any robot for which a URDF is available.

---

## 2. Architecture

### 2.1 Compiler Analogy

Axiom follows the structure of a three-stage compiler:

```
Source language (English)
         |
    Frontend (LLM)
    Parses intent into structured actions
         |
    Intermediate Representation (TaskSpec)
    Structured action descriptions with coordinates
         |
    Optimizer (Axiom gate pipeline)
    Validates physics, computes fixes
         |
    Backend (VLA / robot controller)
    Generates motor commands from validated actions
         |
    Hardware (robot)
```

The **frontend** (LLM) understands language but not physics. The **backend** (VLA or motor controller) can execute motor commands but does not understand language. The **optimizer** (Axiom) connects them by ensuring that every action the backend receives is physically feasible, and feeding structured fixes back to the frontend when it is not.

### 2.2 System Layers

The system is composed of five layers, each with a single responsibility:

| Layer | Module | Input | Output | Responsibility |
|-------|--------|-------|--------|----------------|
| **Codegen** | `codegen.py` | English task description | `list[dict]` of actions | Prompt LLM, parse response, format constraints |
| **Resolver** | `resolve.py` | VLA callable + task string | `ResolveResult` | Orchestrate propose/validate/fix loop |
| **VLA Adapter** | `vla.py` | Action dict(s) | `ActionResult` / `PlanResult` | Translate dicts into SDK calls |
| **SDK** | `sdk.py` | Keyword args or `TaskSpec` | `Result` | Build TaskSpec, run gates, return verdict |
| **Gate Pipeline** | `evidence.py` + `gates/*.py` | `TaskSpec` | `EvidencePacket` | Execute physics checks, compute fixes |

Each layer depends only on the one below it. The codegen layer is optional — users can plug in any action source at the resolver layer.

### 2.3 Data Model

**TaskSpec** (input) is a Pydantic model describing a robotic task:

- `substrate` — the object being manipulated (ID, mass, initial pose)
- `transformation` — target end-effector pose (XYZ position, optional quaternion/RPY orientation, tolerances)
- `constructor` — the robot (ID, base pose, reach limit, payload limit, optional URDF path)
- `environment` — keepout zones (axis-aligned bounding boxes with safety buffer)
- `allowed_adjustments` — flags governing which counterfactual fixes are permitted

**EvidencePacket** (output) is the structured result:

- `verdict` — `CAN` or `HARD_CANT`
- `checks` — ordered list of gate results with measured values
- `counterfactual_fixes` — ranked list of minimal-change fixes with instructions and coordinate patches

---

## 3. Gate Pipeline

### 3.1 Pipeline Ordering and Short-Circuit Behavior

Gates execute in a fixed order. The first failure terminates the pipeline (short-circuit evaluation). This optimises for the common case where one dominant constraint makes further checking unnecessary.

```
IK Feasibility (optional, if URDF provided)
    |
    |— PASS → skip Reachability (IK subsumes it)
    |— FAIL → stop, return HARD_CANT + fixes
    |— SKIP (no URDF) → fall through
    |
Reachability (spherical, fallback)
    |— FAIL → stop
    |
Payload
    |— FAIL → stop
    |
Keepout Zones
    |— FAIL → stop
    |
All passed → CAN
```

When the IK gate passes, the spherical reachability gate is skipped entirely. IK feasibility is strictly more precise — if IK found a valid joint configuration, the target is necessarily within the robot's workspace. This subsumption avoids redundant computation.

### 3.2 Gate 1: IK Feasibility

**Purpose:** Determine whether a valid inverse kinematics solution exists for the target pose.

**When it runs:** Only when `constructor.urdf_path` is provided. Otherwise skipped, and the spherical reachability gate serves as the fallback.

**Solver:** ikpy (pure Python, numpy-based). Loaded lazily on first use.

#### 3.2.1 Multi-Start Algorithm

Single-start IK is prone to local optima — the solver converges to a nearby but infeasible configuration while a valid solution exists elsewhere in the joint space. Axiom addresses this with a deterministic multi-start strategy.

The algorithm generates K=6 initial seed configurations:

- **Seed 0:** Home position (all joints at zero)
- **Seeds 1 through K-1:** Each joint is set to a fraction of its range

For seed index `i` (1-indexed), each active joint `j` with bounds `[lo, hi]` is initialised to:

```
q_j = lo + (hi - lo) * (i / K)
```

This produces evenly spaced seeds across the joint space. For joints with unbounded ranges, the bounds default to `[-π, π]`.

For each seed, the solver runs inverse kinematics and evaluates the result via forward kinematics. The best solution (lowest position error, or lowest combined position + orientation error for oriented targets) is kept.

**Properties:**
- Fully deterministic — same inputs always produce the same solution
- K=6 provides a balance between coverage and computation time
- Reduces false negatives from local optima while keeping wall-clock time under 3 seconds for typical 6-DOF arms

#### 3.2.2 Orientation Checking

When the TaskSpec includes orientation (`target_quat_wxyz` or `target_rpy_rad`), the gate evaluates both position and orientation feasibility.

**Orientation representation:** Internally, all orientations are represented as unit quaternions `[w, x, y, z]`. If RPY is provided, it is converted to a quaternion using the ZYX Euler convention:

```
w = cos(r/2) cos(p/2) cos(y/2) + sin(r/2) sin(p/2) sin(y/2)
x = sin(r/2) cos(p/2) cos(y/2) - cos(r/2) sin(p/2) sin(y/2)
y = cos(r/2) sin(p/2) cos(y/2) + sin(r/2) cos(p/2) sin(y/2)
z = cos(r/2) cos(p/2) sin(y/2) - sin(r/2) sin(p/2) cos(y/2)
```

Quaternions are normalised to unit length by a Pydantic model validator on `TransformationSpec`. If both quaternion and RPY are provided, the quaternion takes precedence.

**Angular distance** between the target and achieved orientation is computed via the rotation matrix trace:

```
R_diff = R_target^T @ R_actual
cos(θ) = clamp((trace(R_diff) - 1) / 2, -1, 1)
θ = arccos(cos(θ))
```

**Pass criteria:**
- Position error ≤ `tolerance_m` (default 0.01 m)
- Orientation error ≤ `orientation_tolerance_rad` (default 0.1745 rad ≈ 10°)

**Reason codes:**
- `NO_IK_SOLUTION` — no joint configuration reaches the target position within tolerance
- `ORIENTATION_MISMATCH` — position is reachable but the required orientation cannot be achieved

#### 3.2.3 Fix Computation

When IK fails and `allowed_adjustments.can_move_target` is true, the gate proposes a `MOVE_TARGET` fix. The fix target is the forward-kinematics result from the best seed — the nearest point the robot can actually reach. This point is transformed from the robot's base frame to the world frame:

```
fix_xyz = fk_position + constructor.base_pose.xyz
```

The fix also includes the achieved quaternion (if orientation was requested), enabling the action source to adjust both position and orientation.

### 3.3 Gate 2: Reachability (Spherical Fallback)

**Purpose:** Check whether the target is within the robot's maximum reach radius.

**When it runs:** Only when the IK gate was skipped (no URDF) or is absent. If IK ran and passed, this gate is skipped.

**Algorithm:** Compute Euclidean distance from `constructor.base_pose` to `transformation.target_pose`. If `distance ≤ max_reach_m`, the gate passes.

**Fix computation:** Project the target onto the reach sphere surface along the line from the base to the target:

```
scale = max_reach_m / distance
projected = base + (target - base) * scale
```

Available fixes (depending on `allowed_adjustments`):
- `MOVE_TARGET` — move the target to the projected point on the sphere
- `MOVE_BASE` — move the robot base toward the target by the overshoot distance
- `CHANGE_CONSTRUCTOR` — suggest a robot with sufficient reach

### 3.4 Gate 3: Payload

**Purpose:** Check whether the object mass is within the robot's payload capacity.

**Algorithm:** If `substrate.mass_kg ≤ constructor.max_payload_kg`, the gate passes.

**Fix computation:**
- `SPLIT_PAYLOAD` — compute the number of trips: `⌈mass / max_payload⌉`
- `CHANGE_CONSTRUCTOR` — suggest a robot with sufficient payload capacity

### 3.5 Gate 4: Keepout Zones

**Purpose:** Check whether the target is outside all forbidden regions.

**Algorithm:** Each keepout zone is an axis-aligned bounding box (AABB) defined by `min_xyz` and `max_xyz`, expanded by the environment's `safety_buffer` on each face. The target is tested against each expanded AABB. First violation triggers failure.

**Expanded AABB test:**
```
for each axis i ∈ {0, 1, 2}:
    if point[i] < zone.min_xyz[i] - buffer: outside
    if point[i] > zone.max_xyz[i] + buffer: outside
all axes inside → violation
```

**Fix computation (minimal escape):** For each axis, compute the distance to the nearest face (low face and high face). The escape direction is the axis requiring the smallest displacement:

```
for each axis i:
    d_lo = point[i] - (min_xyz[i] - buffer)    # distance to low face
    d_hi = (max_xyz[i] + buffer) - point[i]     # distance to high face

keep minimum positive distance across all 6 faces
escape point = move point to that face boundary
```

This produces the smallest possible displacement that moves the target outside the expanded zone.

---

## 4. Counterfactual Fix Engine

### 4.1 Design Principles

Every gate failure produces one or more **counterfactual fixes** — hypothetical minimal changes that would make the action feasible. Fixes are ranked by `delta` (magnitude of change), with the smallest change first.

Each fix carries three representations:

| Field | Type | Consumer |
|-------|------|----------|
| `instruction` | Natural language string | LLMs, human operators |
| `proposed_patch` | Dict with exact coordinates | Code-level integrations |
| `type` | Enum (`MOVE_TARGET`, `SPLIT_PAYLOAD`, etc.) | Programmatic routing |

The dual representation is critical for the closed loop. An LLM reads the `instruction` ("Move target within 0.85m of base, currently 2.3m away") and adjusts its next generation. A deterministic planner reads the `proposed_patch` (`{"target_xyz": [0.26, 0.26, 0.26]}`) and uses the exact coordinates. Both paths converge on a physically feasible action.

### 4.2 Fix Types

| Type | Gate | Description | Patch Contents |
|------|------|-------------|----------------|
| `MOVE_TARGET` | IK, Reachability, Keepout | Move the target to a feasible position | `projected_target_xyz`, optionally `fk_quat_wxyz` |
| `MOVE_BASE` | Reachability | Move the robot closer to the target | `suggested_base_xyz` |
| `SPLIT_PAYLOAD` | Payload | Divide the task into multiple trips | `suggested_payload_split_count` |
| `CHANGE_CONSTRUCTOR` | IK, Reachability, Payload | Use a robot with greater capabilities | `minimum_reach_m` or `minimum_payload_kg` |

### 4.3 Fix Selection in the Resolve Loop

The resolver extracts the top fix from a failed gate result and wraps it as a `Constraint`:

```python
Constraint(
    instruction="No IK solution: position error 1.56m. Nearest reachable: [0.75, 0.57, 0.21]",
    reason="NO_IK_SOLUTION",
    fix_type="MOVE_TARGET",
    proposed_patch={"projected_target_xyz": [0.754633, 0.566005, 0.214379]},
)
```

For multi-step plans, the constraint is extracted from the first blocked step (fail-fast semantics). Constraints accumulate across retries — the VLA callable receives the full history of constraints on each call.

---

## 5. The Resolve Loop

### 5.1 Algorithm

```
function resolve(vla, task, max_retries=3, robot_kwargs):
    constraints ← []
    history ← []

    for i in 0 .. max_retries:
        actions ← vla(task, constraints)

        if |actions| = 1:
            result ← validate_action(actions[0], robot_kwargs)
        else:
            result ← validate_plan(actions, robot_kwargs)

        if not result.allowed:
            constraint ← extract_constraint(result)
            if constraint ≠ null:
                constraints.append(constraint)

        history.append(Attempt(i, actions, result, constraint))

        if result.allowed:
            return ResolveResult(resolved=true, actions, i+1, constraints, history)

        if constraint = null:
            break    // no fix available, cannot improve

    return ResolveResult(resolved=false, actions, |history|, constraints, history)
```

### 5.2 Termination Conditions

The loop terminates in exactly one of three ways:

1. **Success:** All actions pass all gates. Return `resolved=True` with the valid plan. Total attempts: `i + 1`.

2. **No fix available:** A gate failed but no counterfactual fix could be computed (e.g., all adjustment flags are disabled). The loop breaks immediately — retrying without new information would produce the same result.

3. **Max retries exhausted:** The VLA produced `1 + max_retries` plans, none of which passed. Return `resolved=False` with the full history. This bounds computation and prevents infinite loops when the VLA cannot satisfy the constraints.

### 5.3 Constraint Accumulation

Constraints accumulate across iterations. On attempt `i`, the VLA receives all constraints from attempts `0` through `i-1`. This provides the VLA with the complete history of what failed and why, enabling it to avoid repeating the same mistakes.

### 5.4 Plan Gating (Fail-Fast)

For multi-step plans (more than one action), `validate_plan()` gates each step in sequence and stops at the first failure. Only steps up to and including the failed step are validated. This is correct because later steps may depend on earlier ones — if step 2 fails, step 3's feasibility is undefined.

The constraint extracted from a plan failure refers specifically to the blocked step, not the plan as a whole. This gives the VLA targeted feedback about which step to fix.

---

## 6. LLM Codegen Adapter

### 6.1 System Prompt Design

The system prompt provides the LLM with the robot's physical capabilities:

```
You are a robot action planner. Output a JSON array of robot actions.

Robot: ur5e
  - Max reach from base: 0.85 m
  - Max payload: 5.0 kg
  - Base position: origin [0, 0, 0]
  - Workspace: sphere of radius 0.85 m centered at the base

Each action: {"target_xyz": [x, y, z], "mass_kg": <number>}
```

The prompt instructs the LLM to output only a JSON array — no explanation, no markdown, no code. This maximises parse reliability while keeping the prompt concise.

### 6.2 Constraint Injection

When constraints exist from previous failures, they are appended to the user message:

```
IMPORTANT — your previous plan was REJECTED. You MUST fix it.

Physical constraint violations:
  1. [NO_IK_SOLUTION] No IK solution: position error 1.56m.
     → suggested: {"projected_target_xyz": [0.75, 0.57, 0.21]}
  2. [OVER_PAYLOAD] Object mass 10.0 kg exceeds 5.0 kg limit.
     → suggested: {"suggested_payload_split_count": 2}
```

Each constraint includes the reason code, the human-readable instruction, and the structured patch. The LLM can use either representation — language models naturally read the instruction text, while more structured integrations can parse the JSON patch.

### 6.3 Response Parsing

The parser handles common LLM output variations:

1. **Markdown stripping** — removes `` ```json `` and `` ``` `` fences
2. **Single-dict normalisation** — wraps `{...}` in `[{...}]`
3. **Validation** — ensures each action has `target_xyz` as a 3-element list
4. **Extra fields preserved** — the parser does not strip unknown fields, allowing the LLM to annotate actions with labels or metadata

Parse failures (malformed JSON, missing fields) raise `ValueError` and propagate to the caller. The resolver does not catch these — a malformed LLM response is a genuine error, not a constraint violation.

### 6.4 Provider Compatibility

The adapter uses the OpenAI chat completions API, which is supported by:

| Provider | Base URL | Notes |
|----------|----------|-------|
| OpenAI | `https://api.openai.com/v1` | GPT-4o, GPT-4o-mini |
| Groq | `https://api.groq.com/openai/v1` | Free tier, Llama 3.3 70B |
| Together | `https://api.together.xyz/v1` | Open-weight models |
| OpenRouter | `https://openrouter.ai/api/v1` | Multi-provider routing |
| Ollama | `http://localhost:11434/v1` | Local models, no API key |
| vLLM | `http://localhost:8000/v1` | Self-hosted serving |

Configuration via environment variables (`AXIOM_OPENAI_API_KEY`, `AXIOM_OPENAI_BASE_URL`, `AXIOM_CODEGEN_MODEL`) or constructor parameters.

---

## 7. Empirical Validation

### 7.1 Test Suite

The system includes 236 automated tests covering all layers:

| Module | Tests | Coverage |
|--------|-------|----------|
| Gate pipeline (IK, reachability, payload, keepout) | 51 | Gate logic, fixes, edge cases |
| Oriented IK (6-DOF) | 12 | Position + orientation, multi-start determinism |
| SDK | 12 | check/check_simple, bundled URDF, result immutability |
| VLA adapter | 18 | Single action, plan gating, fail-fast |
| Resolver | 24 | First-try success, retry, max retries, constraint accumulation |
| LLM codegen | 33 | Prompt construction, parsing, mocked end-to-end |
| CLI | 23 | Run, sweep, replay, init, JUnit output |
| API | 26 | REST endpoints, sweeps, AI integration |
| Other | 37 | TaskSpec mapping, ROS 2, AI providers |

Test-to-source ratio: 159% (3,363 test lines vs. 2,108 source lines).

### 7.2 End-to-End Demonstration

The following transcript shows a real execution with GPT-4o-mini as the LLM backend and no robot-specific knowledge in the prompt:

```
Task: "Pick up the 0.5kg box from the conveyor at [2.0, 1.5, 0.3]
       and place it at the packing station at [3.0, 0.0, 0.5]"

Robot: UR5e (max reach ~0.85m from base at origin)

Attempt 0 [BLOCKED]:
  LLM proposes: pick [2.0, 1.5, 0.3], place [3.0, 0.0, 0.5]
  Gate: NO_IK_SOLUTION — position error 1.56m
  Fix: nearest reachable [0.75, 0.57, 0.21]

Attempt 1 [BLOCKED]:
  LLM fixes pick to [0.75, 0.57, 0.21] ✓ but place still at [3.0, 0.0, 0.5]
  Gate: NO_IK_SOLUTION — position error 2.07m
  Fix: nearest reachable [0.94, 0.0, 0.27]

Attempt 2 [PASS]:
  LLM fixes place to [0.94, 0.0, 0.27] ✓
  All gates pass — resolved in 3 attempts

Final plan:
  Step 0: move to [0.75, 0.57, 0.21]  (0.5 kg)
  Step 1: move to [0.94, 0.0, 0.27]   (0.5 kg)
```

The LLM initially proposed coordinates 2-3 metres from a robot with 0.85m reach. Axiom caught each infeasible step, computed the nearest reachable point via IK forward kinematics, and fed the fix back. The LLM adopted the corrected coordinates and the plan converged in 3 attempts with zero human intervention.

---

## 8. Integration Surfaces

### 8.1 Python SDK

```python
# One-liner: English → validated actions
from axiom_tfg import prompt_and_resolve
result = prompt_and_resolve("pick up the mug", api_key="sk-...")

# Bring your own VLA/planner
from axiom_tfg import resolve, Constraint
result = resolve(my_callable, "pick up the mug")

# Direct validation
from axiom_tfg import validate_action
r = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35})
```

### 8.2 CLI

```bash
axiom init my-project          # scaffold with examples + CI
axiom run task.yaml --junit    # single check + JUnit XML
axiom sweep task.yaml --n 50   # parameter sweep
axiom replay regressions/      # regression testing
```

### 8.3 REST API

```
POST /runs          — submit TaskSpec, get verdict + evidence
POST /sweeps        — deterministic parameter sweep
POST /ai/generate   — LLM generates TaskSpec from prompt
POST /ai/explain    — LLM explains evidence in plain English
```

### 8.4 ROS 2

Pre-flight proxy for Nav2 `NavigateToPose` actions. Intercepts goals, validates against Axiom gates, forwards only feasible goals to the real navigation stack.

---

## 9. Comparison with Related Work

| System | Validation | Fixes | Closed Loop | Deterministic | LLM Integration |
|--------|-----------|-------|-------------|---------------|-----------------|
| **Axiom-TFG** | IK + reach + payload + keepout | Structured patches + NL | Automatic | Yes | Native |
| MoveIt | Motion planning (full) | None | No | Yes | No |
| PyBullet | Full physics sim | None | No | Yes | No |
| SayCan | Learned affordances | None | Manual | No | Yes |
| Code as Policies | None | None | Manual | N/A | Yes |
| ChatGPT for Robotics | None | None | Human-in-loop | N/A | Yes |
| Inner Monologue | Execution feedback | None | Semi-auto | No | Yes |

Axiom's distinguishing contribution is the combination of deterministic validation, structured fix generation, and automatic closed-loop feedback — with fixes expressed in natural language that LLMs can consume directly.

---

## 10. Limitations and Future Work

### 10.1 Current Limitations

**Endpoint validation only.** Axiom validates target poses, not trajectories. It confirms the robot can reach a point, not that it can get there from its current configuration without collision along the path. This is sufficient for high-level planning validation but not for real-time motion safety.

**AABB keepout zones.** Forbidden regions are axis-aligned bounding boxes. Real environments contain complex geometry (curved surfaces, other robots, humans). Mesh-based collision checking would provide higher fidelity.

**Single robot model bundled.** Only the UR5e URDF ships with the package. Users with other robots must provide their own URDF and configure link names.

**No dynamics.** Gates check static feasibility (can the robot reach this pose with this load?) but not dynamic feasibility (can it accelerate fast enough, does the trajectory respect velocity/torque limits?).

### 10.2 Future Directions

**Trajectory validation.** Extend from endpoint checking to path checking — validate that the robot can move from configuration A to configuration B without collision or joint limit violation. This would enable validation of VLA delta-action sequences, not just high-level plans.

**Mesh collision gate.** Replace AABB keepout zones with trimesh or PyBullet-based collision checking against environment meshes and point clouds.

**Robot library.** Bundle URDFs and tuned parameters for common arms (UR3/5/10/16e, Franka Panda, Kinova Gen3, xArm6) to enable zero-configuration usage for the majority of deployed robots.

**State tracking.** Maintain a world model across plan execution — track what the robot is holding, how the environment has changed, and validate subsequent actions against the updated state rather than the initial state.

**VLA delta integration.** For low-level VLAs that output incremental actions (Octo, RT-2, pi0), accumulate deltas into predicted trajectories and validate the trajectory endpoint and path, bridging the gap between high-level and low-level action sources.

---

## 11. Conclusion

Axiom-TFG demonstrates that deterministic physics validation, combined with structured counterfactual fixes expressed in natural language, enables a closed loop between LLM-based code generation and physical robot execution. The system validates robot actions against kinematic, payload, and spatial constraints; produces actionable fixes when constraints are violated; and feeds those fixes back to the LLM automatically — converging on a physically feasible plan without human intervention.

The practical implication is that a non-expert can describe a robot task in plain English and receive a validated action sequence, without understanding kinematics, workspace limits, or payload constraints. The LLM handles intent; Axiom handles physics; the loop connects them.

The system is implemented as an open-source Python library (MIT license) with 236 tests, CLI tooling, REST API, and ROS 2 integration. It is available at `pip install axiom-tfg`.

---

## Appendix A: Code Statistics

| Component | Files | Lines |
|-----------|-------|-------|
| Core library (`axiom_tfg/`) | 12 | 2,108 |
| CLI (`axiom_cli/`) | 3 | 563 |
| API server (`axiom_server/`) | 4 | 1,121 |
| ROS 2 integration (`ros2/`) | 5 | 310 |
| Test suite (`tests/`) | 15 | 3,363 |
| **Total** | **39** | **7,465** |

## Appendix B: Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| ikpy | ≥3.3 | Inverse kinematics solver |
| numpy | ≥1.24 | Numerical computation |
| pydantic | ≥2.0 | Schema validation |
| typer | ≥0.9 | CLI framework |
| fastapi | ≥0.110 | REST API |
| openai | ≥1.0 | LLM API client |
| pyyaml | ≥6.0 | YAML parsing |

## Appendix C: API Reference

### `prompt_and_resolve(task, *, api_key, model, robot, max_reach_m, max_payload_kg, max_retries, keepout_zones) → ResolveResult`

One-liner: natural language to validated robot actions.

### `resolve(vla, task, *, max_retries, robot, **robot_kwargs) → ResolveResult`

Closed-loop resolver. `vla` is any callable with signature `(str, list[Constraint]) → list[dict]`.

### `validate_action(action, *, robot, **kwargs) → ActionResult`

Gate a single action dict. Returns `allowed`, `verdict`, `reason`, `fix`, `evidence`.

### `validate_plan(actions, *, robot, **kwargs) → PlanResult`

Gate a sequence of actions with fail-fast semantics.

### `check_simple(*, target_xyz, mass_kg, robot, **kwargs) → Result`

Direct feasibility check with keyword arguments.

### `make_codegen_vla(*, api_key, model, robot, **kwargs) → Callable`

Factory for LLM-backed VLA callables.
