# Shrinking the Failure Loop: A Repair-and-Proof Architecture for Production-Ready Physical AI

**Version 0.2.0 — March 2026**

---

## Abstract

Generating candidate robot behaviors is getting cheap. Foundation models, vision-language-action (VLA) systems, and code-generation pipelines can produce plausible action sequences, controller policies, and task decompositions faster than ever. But shipping those behaviors — making them reliable, safe, repeatable, and debuggable — is still expensive. The bottleneck has shifted: the rate limiter in Physical AI is no longer producing behaviors, it is hardening them into something dependable enough to deploy.

The core problem is what happens after a behavior fails. Most systems emit non-actionable outputs — "failed," "collision," "no solution," "timed out" — and a human does the expensive work: interpreting the failure, guessing a correction, rerunning the test, and repeating until it works. This is the human translation tax, and it is where time and headcount go.

We present Axiom, a system that replaces manual failure interpretation with an artifact-producing repair loop. When a behavior fails, Axiom computes the smallest change that would make it succeed, expresses that change in both machine-readable coordinates and natural language, feeds it back to the behavior source automatically, and records structured evidence that can be replayed and used to gate regressions. The mechanism is demonstrated through a physics validation layer for robot actions — five deterministic gates covering kinematics, payload, and spatial constraints — but the architecture is designed around the loop, not the gates. The gates are the first instantiation; the repair-and-proof pattern is the point.

The system is implemented as an open-source Python library (279 tests, MIT licensed) with CLI, REST API, and ROS 2 integration.

---

## 1. The Structural Shift

### 1.1 Behavior Generation Is Now Cheap

Two years ago, getting a robot to attempt a new task required a specialist writing code on a teach pendant or in an offline programming environment. Today, multiple systems can generate candidate behaviors from natural language or demonstration:

- **LLM code generation.** Code as Policies [1], ChatGPT for Robotics [2], and ProgPrompt [3] produce syntactically valid robot programs from language instructions.
- **Vision-language-action models.** NVIDIA's Groot N1 [4], RT-2 [5], and the rapidly expanding VLA field [6] fuse perception, language, and motor control into end-to-end policies.
- **Reward synthesis.** Language to Rewards [7] has LLMs define reward functions that are optimized into motor policies, achieving 90% task completion.
- **Task planners.** SayCan [8] and Text2Motion [9] decompose high-level instructions into grounded action sequences.

The supply of candidate behaviors is no longer scarce. A team can generate dozens of plausible action sequences in the time it used to take to hand-code one.

### 1.2 Production Readiness Is Still Expensive

Even when candidate behaviors are abundant, the hard part remains:

- Making the behavior **reliable** under real-world variation
- Making it **safe** under physical constraints
- Making it **robust** over time as environments change
- Making it **repeatable** across hardware and deployment sites
- Making it **debuggable** and **regressable** when it breaks

These are not generation problems. They are hardening problems. And they are where the calendar time goes.

### 1.3 The Bottleneck Has Moved

This is the structural claim:

> **The new rate limiter in Physical AI is no longer generating behaviors; it is validating, repairing, and hardening them fast enough to ship.**

Once this is true — and the evidence from both research [9][10] and industry [11][12] suggests it is — the most valuable tools are the ones that:

- Reduce wasted trials
- Turn failures into actionable next steps
- Create artifacts that prevent regressions
- Bound uncertainty enough to let teams deploy with confidence

---

## 2. The Failure Loop Problem

### 2.1 The Common Pattern

Most robotics stacks follow the same failure pattern:

1. A behavior is proposed (by a human, policy, planner, or generator)
2. It fails (in simulation, hardware-in-the-loop, or on the real robot)
3. The system emits a non-actionable failure: "failed," "collision," "no IK solution," "timed out"
4. A human performs the expensive translation:
   - Interpret the failure
   - Figure out what could change
   - Guess a correction
   - Rerun the test
5. Repeat until it works — then repeat again when it regresses

This is the **human translation tax**. It is the hidden cost center in every Physical AI deployment. Production readiness is expensive because turning failures into the next valid attempt is still mostly manual.

### 2.2 Three Loops

There are three loops that matter in Physical AI development:

**Loop A: Generate → Try → Observe** (the creation loop). This loop is now fast because models can generate a lot. It is not the bottleneck.

**Loop B: Fail → Diagnose → Repair → Retry** (the hardening loop). This loop is usually slow and human-bound. A failure produces a log entry or a vague error. A human stares at it, forms a hypothesis, makes a change, and reruns. Each cycle takes minutes to hours. This is where most calendar time is spent between "it works in the lab" and "it works in production."

**Loop C: Fix → Prevent Regression** (the trust loop). This loop is about artifacts: can you replay the failure? Can you prove it's fixed? Can you gate the pipeline so the same failure doesn't recur? Without this loop, every deployment is a leap of faith.

The bottleneck is in Loops B and C. Making the hardening loop cheap and the trust loop systematic is how you get acceleration that compounds.

### 2.3 Why It Compounds

Each repair cycle, done correctly, produces three things:

1. **A corrected behavior** — the immediate value
2. **A structured record** of what failed, why, and what fixed it — the diagnostic value
3. **A regression artifact** — a replayable test case that blocks reintroduction of the same failure

The corrected behavior is consumed once. The record and the regression artifact accumulate. Over time, the system builds a library of known failure modes and proven corrections. New behaviors are validated against this library before they reach hardware. The cost of hardening decreases as the library grows.

This is the compounding: each failure, properly processed, makes the next failure cheaper to handle. But only if the loop produces artifacts — not just fixes.

---

## 3. What "Shrinking the Loop" Requires

### 3.1 From Non-Actionable to Actionable

The minimum viable improvement is turning:

```
"failed" → manual diagnosis → manual fix → rerun
```

into:

```
"failed" → concrete next attempt + replayable proof → regression gate
```

This requires four capabilities:

1. **Deterministic validation.** Given a proposed behavior and a set of physical constraints, compute a definitive pass/fail verdict. Not "probably fine" — definitively yes or definitively no. Same input, same output, every time.

2. **Structured fix computation.** When validation fails, compute the smallest change that would make the behavior pass. Express the fix in two forms: machine-readable (exact coordinates, parameters) and human-readable (natural language instruction). The dual representation matters because behavior sources vary — an LLM reads the instruction, a deterministic planner reads the coordinates, a VLA reads the waypoint correction.

3. **Automatic feedback.** Feed the fix back to the behavior source without human intervention. The source regenerates with the constraint. The loop repeats until the behavior passes or the retry budget is exhausted.

4. **Proof artifacts.** Every validation cycle produces a structured evidence record: what was proposed, which constraints were checked, what was measured, what failed, what fix was computed, and what the behavior source did next. This record is the proof. It can be replayed, audited, and used as a regression gate.

### 3.2 What Proof Artifacts Look Like

In deep tech — especially embodied systems — trust is the currency. Teams don't trust explanations. They trust artifacts.

A proof artifact in Axiom is an **EvidencePacket**: a timestamped, structured JSON record containing:

| Field | Content | Purpose |
|-------|---------|---------|
| `verdict` | `CAN` or `HARD_CANT` | Binary decision |
| `checks` | Ordered list of gate results | Which constraints were evaluated |
| `measured_values` | Per-gate measurements (distances, masses, errors) | Reproducible diagnostics |
| `reason_code` | Machine-readable failure cause | Programmatic routing |
| `counterfactual_fixes` | Ranked list of minimal-change corrections | Actionable next step |
| `validation_level_reached` | `L0` (endpoint) or `L1` (path) | Depth of verification |
| `created_at` | ISO timestamp | Audit trail |

This is not a log line. It is a structured, machine-readable record that answers: what was checked, what was measured, what failed, and what would fix it. It can be:

- **Replayed** — run the same input through the same gates and confirm the same result
- **Diffed** — compare evidence packets before and after a fix to confirm the fix worked
- **Gated** — block a deployment pipeline if a known regression case fails
- **Audited** — provide the compliance documentation that ISO 10218:2025 requires [13][14]

---

## 4. Architecture

### 4.1 The Repair Loop

The core architecture is a bounded repair loop between a behavior source and a validation layer:

```
Behavior source proposes action(s)
        |
        v
Validation layer checks against constraints
        |
    +---+---+
    |       |
  PASS    FAIL
    |       |
    v       v
 Execute  Compute minimal fix
            |
            v
        Feed fix back as constraint
            |
            v
        Behavior source re-proposes
            |
            v
        (repeat, bounded by retry budget)
```

The behavior source can be anything: an LLM generating action sequences, a VLA outputting end-effector deltas, a human operator entering coordinates, or a deterministic planner. The architecture is agnostic to the source — it only requires that the source can accept constraints and produce a revised proposal.

The validation layer can check any constraint that is deterministically computable from the robot's model and the environment description. The current implementation checks physics constraints; the architecture supports any domain where "is this valid?" and "what's the smallest change to make it valid?" are both answerable.

### 4.2 System Layers

```
English task ─────────────────────────────────────────> Robot
      |                                                   ^
      v                                                   |
 [Codegen]  LLM parses intent into actions                |
      |                                                   |
      v                                                   |
 [Resolver] Orchestrates propose/validate/fix loop        |
      |                                                   |
      v                                                   |
 [VLA Adapter] Normalizes action dicts into gate calls    |
      |                                                   |
      v                                                   |
 [SDK] Builds TaskSpec, routes to gate pipeline           |
      |                                                   |
      v                                                   |
 [Gate Pipeline] Deterministic physics checks ────────────+
      |
      v
 [Evidence] Structured proof artifacts (EvidencePacket)
```

Each layer depends only on the one below it. The codegen layer is optional — any behavior source can plug in at the resolver layer.

### 4.3 Data Model

**Input: TaskSpec.** A Pydantic model describing a proposed robotic action:

- `substrate` — the object being manipulated (ID, mass, initial pose)
- `transformation` — target end-effector pose (XYZ, optional orientation as quaternion or RPY, tolerances, waypoints)
- `constructor` — the robot (ID, base pose, reach limit, payload limit, optional URDF)
- `environment` — keepout zones (axis-aligned bounding boxes with safety buffer)
- `allowed_adjustments` — flags governing which fix types are permitted

**Output: EvidencePacket.** The structured result:

- `verdict` — `CAN` or `HARD_CANT`
- `checks` — ordered list of gate results with measured values and validation levels
- `counterfactual_fixes` — ranked list of minimal-change fixes, each carrying a natural-language instruction, a machine-readable patch, and a fix type
- `validation_level_reached` — highest level fully passed (`L0` = endpoint, `L1` = path)

### 4.4 Constraint Accumulation

The resolver maintains a list of constraints across iterations. On attempt `i`, the behavior source receives all constraints from attempts `0` through `i-1`. This provides the complete history of what failed and why, enabling the source to avoid repeating the same mistakes.

Each constraint carries:

```python
Constraint(
    instruction="No IK solution: position error 1.56m. Nearest reachable: [0.75, 0.57, 0.21]",
    reason="NO_IK_SOLUTION",
    fix_type="MOVE_TARGET",
    proposed_patch={"projected_target_xyz": [0.754633, 0.566005, 0.214379]},
)
```

The `instruction` is what an LLM reads. The `proposed_patch` is what a deterministic system uses. Both paths converge on the same correction.

---

## 5. The Gate Pipeline: First Instantiation

The repair loop architecture is general. The first instantiation validates robot actions against physical constraints through five deterministic gates.

### 5.1 Why Physics Gates First

Physics validation is the right starting point for three reasons:

1. **The failures are expensive.** A robot that exceeds its reach hits joint limits. A robot that enters a keepout zone triggers an e-stop. A robot that lifts beyond its payload capacity drops the part. Each failure costs downtime, risks hardware damage, and in collaborative settings, raises safety concerns.

2. **The fixes are computable.** Unlike semantic failures ("the robot picked up the wrong object"), physics failures have mathematically precise corrections. If the target is out of reach, the nearest reachable point can be computed from the robot's kinematics. If the payload is too heavy, the number of required trips can be computed from the mass ratio. The fix is deterministic, not heuristic.

3. **The evidence is clear.** Research shows that adding geometric feasibility checking raises task success from 13% to 82% [9]. The improvement is not marginal — it is the difference between a demo and a working system.

### 5.2 Gate Ordering and Short-Circuit

Gates execute in a fixed order. The first failure terminates the pipeline (short-circuit evaluation):

```
IK Feasibility (if URDF provided)
    |— PASS → skip Reachability (IK subsumes it)
    |— FAIL → stop, compute fix, return evidence
    |— SKIP (no URDF) → fall through
    v
Reachability (spherical fallback)
    |— FAIL → stop
    v
Payload
    |— FAIL → stop
    v
Keepout Zones (endpoint)
    |— FAIL → stop
    v
Path Keepout (if waypoints provided)
    |— FAIL → stop
    v
All passed → CAN
```

### 5.3 Gate Details

**Gate 1: IK Feasibility.** Determines whether a valid inverse kinematics solution exists for the target pose. Uses a deterministic multi-start algorithm with K=6 seed configurations spread evenly across the joint space, evaluated via ikpy. Supports both position-only and position+orientation targets. When it fails, the fix target is the forward-kinematics result of the best seed — the nearest point the robot can actually reach.

**Gate 2: Reachability (spherical fallback).** Checks whether the target is within the robot's maximum reach radius. Runs only when IK is skipped (no URDF). Fix: project the target onto the reach sphere surface along the line from base to target.

**Gate 3: Payload.** Checks whether the object mass is within the robot's payload capacity. Fix: compute the number of trips (`ceil(mass / max_payload)`) or suggest a robot with greater capacity.

**Gate 4: Keepout Zones.** Checks whether the target is outside all forbidden regions (axis-aligned bounding boxes expanded by safety buffer). Fix: compute the minimal escape — the smallest displacement that moves the target outside the expanded zone boundary.

**Gate 5: Path Keepout.** Checks whether the motion path (defined by waypoints) crosses any forbidden region. Fix: compute a rerouted waypoint that avoids the zone.

### 5.4 Fix Types

Every gate failure produces one or more counterfactual fixes, ranked by delta (smallest change first):

| Fix Type | Gates | Patch Contents |
|----------|-------|----------------|
| `MOVE_TARGET` | IK, Reachability, Keepout, Path Keepout | `projected_target_xyz`, optionally `fk_quat_wxyz` |
| `MOVE_BASE` | Reachability | `suggested_base_xyz` |
| `SPLIT_PAYLOAD` | Payload | `suggested_payload_split_count`, `split_mass_kg` |
| `CHANGE_CONSTRUCTOR` | IK, Reachability, Payload | `minimum_reach_m` or `minimum_payload_kg` |

Each fix carries both a human-readable instruction and a machine-readable patch. The dual representation is what closes the loop: an LLM reads the instruction and adjusts; a deterministic planner reads the patch and uses exact coordinates.

### 5.5 Supported Robots

Five robots ship with real kinematic parameters and bundled URDFs:

| Robot | DOF | Reach (m) | Payload (kg) |
|-------|-----|-----------|-------------|
| UR3e | 6 | 0.50 | 3.0 |
| UR5e | 6 | 1.85 | 5.0 |
| UR10e | 6 | 1.30 | 12.5 |
| Franka Panda | 7 | 0.855 | 3.0 |
| KUKA iiwa 14 | 7 | 0.82 | 14.0 |

Pass `robot="franka"` to any function — URDF, joint limits, reach, payload, and link names are set automatically.

---

## 6. Concrete Example: The Loop in Action

A UR5e robot tends a CNC machine. The behavior source (an LLM, or in the demo, a deterministic mock) proposes three actions. Each hits a different physics wall. The loop converges in four attempts:

```
Task: "Pick a steel blank from the parts bin, load it into the CNC
       machine, and unload the finished part to the inspection table."

Attempt 0 — behavior source proposes 3 actions:
  1. Pick from parts bin    target=[2.50, 0.00, 0.30]  2.0 kg
  2. Load into CNC machine  target=[0.50, 0.50, 0.40]  2.0 kg
  3. Unload to inspection   target=[0.40, -0.30, 0.20] 8.0 kg

  FAIL: IK feasibility — target is 2.52m away, robot reaches 1.85m
  Evidence: { reason: "NO_IK_SOLUTION", measured: { position_error: 1.56 } }
  Fix: MOVE_TARGET → [0.94, 0.00, 0.21]
  Artifact: EvidencePacket written, replayable

Attempt 1 — source applies fix to action 1:
  1. Pick from parts bin    target=[0.94, 0.00, 0.21]  2.0 kg  (fixed)
  2. Load into CNC machine  target=[0.50, 0.50, 0.40]  2.0 kg
  3. Unload to inspection   target=[0.40, -0.30, 0.20] 8.0 kg

  FAIL: Keepout zone — target is inside safety cage [0.3-0.8, 0.3-0.8, 0-1.0]
  Evidence: { reason: "IN_KEEP_OUT_ZONE", measured: { zone: "safety_cage" } }
  Fix: MOVE_TARGET → [0.28, 0.50, 0.40]
  Artifact: EvidencePacket written, replayable

Attempt 2 — source applies fix to action 2:
  1. Pick from parts bin    target=[0.94, 0.00, 0.21]  2.0 kg
  2. Load into CNC machine  target=[0.28, 0.50, 0.40]  2.0 kg  (fixed)
  3. Unload to inspection   target=[0.40, -0.30, 0.20] 8.0 kg

  FAIL: Payload — object mass 8.0 kg exceeds 5.0 kg limit
  Evidence: { reason: "OVER_PAYLOAD", measured: { mass: 8.0, limit: 5.0 } }
  Fix: SPLIT_PAYLOAD → 2 trips of 4.0 kg each
  Artifact: EvidencePacket written, replayable

Attempt 3 — source splits heavy action into 2 trips:
  1. Pick from parts bin    target=[0.94, 0.00, 0.21]  2.0 kg
  2. Load into CNC machine  target=[0.28, 0.50, 0.40]  2.0 kg
  3. Unload part (trip 1)   target=[0.30, -0.15, 0.20] 4.0 kg  (split)
  4. Unload part (trip 2)   target=[0.40, -0.30, 0.20] 4.0 kg  (split)

  PASS: All gates pass — plan is physically valid.
```

Three different failure types. Three different fix types. Four attempts. Zero human intervention. Each attempt produced a structured evidence record that can be replayed and used as a regression case.

This is one scenario on one robot with one set of gates. The pattern — fail, compute fix, feed back, record evidence — is the same regardless of what generates the behavior and what validates it.

---

## 7. The Trust Loop: Evidence and Regression

### 7.1 Why Artifacts Matter More Than Fixes

The fix gets you from attempt N to attempt N+1. That's immediate value. But the artifact is what makes the system trustworthy over time.

Without artifacts:
- "It works" is a claim, not a proof
- A regression is discovered in production
- The debugging cycle starts from zero

With artifacts:
- "It works" is backed by a structured record of what was checked and measured
- A regression is caught by replaying known failure cases in CI
- The debugging cycle starts with the exact constraint that was violated

### 7.2 Regression Gating

The CLI supports regression replay:

```bash
axiom replay regressions/ --out artifacts/    # replay all known failure cases
```

Each file in `regressions/` is a TaskSpec that previously failed. The replay runs it through the current gate pipeline and confirms either that it still fails (the constraint still holds) or that it now passes (the fix has been incorporated). JUnit XML output (`--junit`) integrates with standard CI pipelines.

This is Loop C — the trust loop. Every failure that gets processed through the repair loop can become a regression case. The library of regression cases grows monotonically. Confidence in the system is proportional to the size and coverage of this library.

### 7.3 Validation Levels

Evidence packets carry a `validation_level_reached` field:

| Level | Meaning | Gates |
|-------|---------|-------|
| `L0` | Endpoint feasibility confirmed | IK/Reachability, Payload, Keepout |
| `L1` | Path feasibility confirmed | All L0 gates + Path Keepout |

This lets downstream consumers make graded decisions: "L0 is sufficient for high-level planning; L1 is required before commanding the real robot." The level system is extensible — future gates (trajectory dynamics, collision meshes) would add L2, L3, etc.

---

## 8. Integration Surfaces

### 8.1 Python SDK

```python
# One-liner: English → validated robot actions
from axiom_tfg import prompt_and_resolve
result = prompt_and_resolve("pick up the mug", api_key="sk-...")

# Bring your own behavior source
from axiom_tfg import resolve, Constraint
def my_vla(task: str, constraints: list[Constraint]) -> list[dict]:
    # constraints[-1].instruction has the fix in English
    # constraints[-1].proposed_patch has exact coordinates
    return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35}]
result = resolve(my_vla, "pick up the mug")

# Direct validation (no loop)
from axiom_tfg import validate_action, check_simple
r = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35})
r = check_simple(target_xyz=[5.0, 5.0, 5.0])  # → HARD_CANT + fix
```

### 8.2 CLI

```bash
tfg demo-factory                              # CNC tending demo
tfg demo-factory --live -m gpt-4o-mini        # same demo, real LLM
axiom run task.yaml --junit                   # single check + JUnit XML
axiom sweep task.yaml --n 50 --seed 1337      # parameter sweep
axiom replay regressions/ --out artifacts/    # regression replay
```

All commands exit 0 on pass, 2 on fail. JUnit XML for CI integration.

### 8.3 REST API

```
POST /runs          — submit TaskSpec, get verdict + evidence
POST /sweeps        — deterministic parameter sweep
POST /ai/generate   — LLM generates TaskSpec from prompt
POST /ai/explain    — LLM explains evidence in plain English
```

### 8.4 ROS 2

Pre-flight proxy for Nav2 `NavigateToPose` actions. Intercepts goals, validates against Axiom gates, forwards only feasible goals to the navigation stack.

### 8.5 LLM Codegen Adapter

The codegen adapter wraps any OpenAI-compatible API (OpenAI, Groq, Together, Ollama, vLLM) as a behavior source for the resolve loop. The system prompt provides the robot's physical capabilities; constraint injection appends failure details and fix suggestions to subsequent prompts. Response parsing handles markdown fences, single-dict normalization, and field validation.

---

## 9. Comparison with Related Work

### 9.1 Against Existing Systems

| System | Deterministic | Computes Fixes | Closes Loop | Produces Evidence |
|--------|:---:|:---:|:---:|:---:|
| **Axiom** | **Yes** | **Yes** | **Yes** | **Yes** |
| Isaac Sim / PyBullet | Yes | No | No | Partial (logs) |
| MoveIt / OMPL | Yes | No | No | No |
| SayCan [8] | No | No | No | No |
| Text2Motion [9] | Partially | No | Partially | No |
| Code as Policies [1] | No | No | No | No |
| VerifyLLM [15] | No | No | No | Partial |
| T3 Planner [16] | Partially | Partially | Yes | No |

The distinguishing contribution is the combination: deterministic validation, structured fix computation, automatic closed-loop feedback, and proof artifacts — all in a single mechanism.

### 9.2 Against "Just Use Simulation"

Simulation catches physics failures but does not compute fixes. When Isaac Sim shows a collision, a human interprets the visualization, guesses a correction, and reruns. That's Loop B with a human in it. Axiom's contribution is removing the human from Loop B — the system computes the correction itself.

Simulation also requires significant setup (environment modeling, asset preparation, GPU compute). Axiom's gates run in milliseconds on a CPU with no simulation environment. This makes validation fast enough to run inside the LLM's generation loop, not as a separate offline step.

The two are complementary, not competitive. Axiom handles constraint checking and fix computation at generation speed; simulation handles high-fidelity dynamics and contact physics at validation depth. A production pipeline would use both.

### 9.3 Against "Just Use Agents"

Autonomous agents can orchestrate repair attempts, but agents are only useful when bounded by verification, evidence, and rollback. An unbounded agent that retries without structure just generates more noise.

Axiom provides the bounded substrate for agent-driven repair:

- The agent proposes candidate adjustments
- Deterministic gates accept or reject
- The system chooses the smallest valid adjustment
- Evidence is recorded
- Regressions are gated

This is a productive role for agents: constrained search over a trusted validation layer, not unconstrained generation.

---

## 10. Why This Gets More Valuable, Not Less

The thesis strengthens as the field advances:

**More behavior generation → more need for hardening.** As models get better at generating candidate behaviors, more candidates are attempted. More candidates means more edge cases, more failures, more regressions. The hardening layer processes more volume and accumulates more regression cases.

**More deployment sites → more environment variation.** A behavior that works in one factory may fail in another due to different workspace layouts, different keepout zones, different robot configurations. The validation layer is parametric in both the robot and the environment, so it handles this variation without retraining.

**More regulatory pressure → more need for evidence.** ISO 10218:2025 requires explicit functional safety documentation for every robot application [13][14]. Evidence packets are exactly the documentation artifact compliance demands. As regulation tightens, the value of structured, auditable proof artifacts increases.

**More VLAs → more need for deterministic safety.** VLA models are powerful but opaque. They can hallucinate object locations under visual ambiguity [10]. A deterministic physics gate catches infeasible actions regardless of the model's confidence. As VLA deployment accelerates, the demand for an independent safety check — one that doesn't depend on the same model it's verifying — grows proportionally.

---

## 11. Limitations and Future Work

### 11.1 Current Limitations

**Endpoint validation only.** The current gates validate target poses, not full trajectories. They confirm the robot can reach a point, not that it can get there from its current configuration without collision along the entire path. This is sufficient for high-level planning but not for real-time motion safety.

**AABB keepout zones.** Forbidden regions are axis-aligned bounding boxes. Real environments contain complex geometry. Mesh-based collision checking would provide higher fidelity.

**No dynamics.** Gates check static feasibility (can the robot reach this pose with this load?) but not dynamic feasibility (velocity limits, torque limits, acceleration constraints).

**Physics gates only.** The current instantiation validates physical constraints. Semantic constraints ("pick up the *red* mug, not the blue one") and temporal constraints ("do A before B") are not yet covered.

### 11.2 Future Directions

**Trajectory validation.** Extend from endpoint checking to full-path checking — validate that the robot can move from configuration A to configuration B without collision or joint limit violation along the way.

**Mesh collision.** Replace AABB keepout zones with trimesh or physics-engine-based collision checking against environment meshes and point clouds.

**Semantic gates.** Add gates that validate task semantics — object identity, ordering constraints, preconditions — using structured scene representations rather than physics alone.

**State tracking.** Maintain a world model across plan execution — track what the robot is holding, how the environment has changed, and validate subsequent actions against the updated state.

**VLA delta integration.** For low-level VLAs that output incremental actions (Octo, RT-2, pi0), accumulate deltas into predicted trajectories and validate the trajectory endpoint and path.

**Regression learning.** Use the growing library of (failure, fix, evidence) tuples to identify patterns — common failure modes for specific robots, environments, or task types — and proactively flag likely failures before the behavior source even proposes them.

---

## 12. Conclusion

The bottleneck in Physical AI is shifting from behavior generation to behavior hardening. Producing candidate behaviors is cheap and getting cheaper. Making those behaviors reliable, safe, repeatable, and auditable is where the time goes.

The core mechanism Axiom provides is simple: when a behavior fails, compute the smallest change that would make it succeed, feed it back automatically, and record structured evidence. This mechanism — the artifact-producing repair loop — shrinks the hardening loop (Loop B) by removing the human translation step, and systematizes the trust loop (Loop C) by producing replayable proof artifacts that gate regressions.

The physics validation layer is the first instantiation: five deterministic gates, five robots, structured evidence packets, closed-loop feedback. It works today — 279 tests, real LLM integration, CLI/API/ROS 2 surfaces. But the gates are not the point. The point is the pattern: **make failures produce the next valid attempt and a proof trail that can be replayed and used to block regressions.**

That pattern applies wherever behaviors are generated and must be hardened. Robot arms are the starting point. The loop is the product.

---

## References

[1] Liang, J. et al. "Code as Policies: Language Model Programs for Embodied Control." arXiv:2209.07753, 2022.

[2] Vemprala, S. et al. "ChatGPT for Robotics: Design Principles and Model Abilities." Microsoft Research, 2023.

[3] Singh, I. et al. "ProgPrompt: Generating Situated Robot Task Plans using Large Language Models." arXiv:2209.11302, 2022.

[4] NVIDIA. "NVIDIA Releases New Physical AI Models as Global Partners Unveil Next-Generation Robots." NVIDIA Newsroom, 2025.

[5] Brohan, A. et al. "RT-2: Vision-Language-Action Models Transfer Web Knowledge to Robotic Control." arXiv:2307.15818, 2023.

[6] "Vision-Language-Action Models: Concepts, Progress, Applications and Challenges." arXiv:2505.04769, 2025.

[7] Yu, W. et al. "Language to Rewards for Robotic Skill Synthesis." arXiv:2306.08647, 2023.

[8] Ahn, M. et al. "Do As I Can, Not As I Say: Grounding Language in Robotic Affordances." arXiv:2204.01691, 2022.

[9] Lin, K. et al. "Text2Motion: From Natural Language Instructions to Feasible Plans." arXiv:2303.12153, 2023.

[10] Kim, J. et al. "Modular Safety Guardrails Are Necessary for Foundation-Model-Enabled Robots in the Real World." arXiv:2602.04056, 2026.

[11] Mordor Intelligence. "Robot Software Market — Size, Share & Industry Forecast." 2026.

[12] MarketsandMarkets. "Artificial Intelligence Robots Market Size, Share, Industry Growth, Trends & Analysis, 2030." 2025.

[13] ANSI Blog. "ISO 10218-1:2025 — Robots And Robotic Devices Safety." 2025.

[14] Robotics 24/7. "A3 revises ISO 10218 robot safety standards for 2025." 2025.

[15] "VerifyLLM: LLM-Based Pre-Execution Task Plan Verification for Robots." arXiv:2507.05118, 2025.

[16] "T3 Planner: A Self-Correcting LLM Framework for Robotic Motion Planning with Temporal Logic." arXiv:2510.16767, 2025.

---

## Appendix A: Code Statistics

| Component | Files | Lines |
|-----------|-------|-------|
| Core library (`axiom_tfg/`) | 17 | ~2,500 |
| CLI (`axiom_cli/`) | 3 | ~560 |
| API server (`axiom_server/`) | 4 | ~1,120 |
| ROS 2 integration (`ros2/`) | 5 | ~310 |
| Test suite (`tests/`) | 15+ | ~3,500 |

## Appendix B: Dependencies

| Package | Purpose |
|---------|---------|
| ikpy | Inverse kinematics solver |
| numpy | Numerical computation |
| pydantic | Schema validation |
| typer | CLI framework |
| fastapi | REST API |
| openai | LLM API client |
| pyyaml | YAML parsing |

## Appendix C: The Resolve Algorithm

```
function resolve(vla, task, max_retries=3, robot_kwargs):
    constraints <- []
    history <- []

    for i in 0 .. max_retries:
        actions <- vla(task, constraints)
        result <- validate(actions, robot_kwargs)

        if not result.allowed:
            constraint <- extract_constraint(result)
            if constraint != null:
                constraints.append(constraint)

        history.append(Attempt(i, actions, result, constraint))

        if result.allowed:
            return ResolveResult(resolved=true, actions, i+1, constraints, history)

        if constraint = null:
            break    // no fix available, cannot improve

    return ResolveResult(resolved=false, actions, |history|, constraints, history)
```

Termination: (1) all gates pass, (2) no fix available (retrying would be pointless), or (3) retry budget exhausted. The loop is guaranteed to terminate.
