# axiom-tfg

[![CI](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**Tell a robot what to do in plain English. Axiom makes sure it's physically possible.**

LLMs can generate robot code. But they don't know your robot's reach limits, payload capacity, or keepout zones. Axiom is the physics layer between the LLM and the robot — it validates every action, and when something is infeasible, feeds the fix back to the LLM automatically until the plan works.

```python
from axiom_tfg import prompt_and_resolve

result = prompt_and_resolve(
    "pick up the mug and put it on the shelf",
    api_key="sk-...",       # any OpenAI-compatible API
)

if result.resolved:
    for action in result.actions:
        robot.move_to(action["target_xyz"])
```

English in, physically valid robot actions out. No robotics expertise needed.

## How it works

```
"pick up the mug and put it on the shelf"
                  |
         LLM generates actions
         [pick at (0.3, -0.2, 0.1), place at (2.5, 1.0, 2.0)]
                  |
         Axiom validates each action
         pick: OK  |  place: BLOCKED — out of reach
                  |
         Fix fed back to LLM: "move target within 0.85m"
                  |
         LLM regenerates with constraint
         [pick at (0.3, -0.2, 0.1), place at (0.5, 0.3, 0.6)]
                  |
         Axiom validates — all pass
                  |
         Execute with confidence
```

The LLM handles **what you want**. Axiom handles **what's physically real**. The fix loop closes automatically — both sides speak language.

## Install

```bash
pip install -e ".[dev]"    # from source, Python 3.11+
```

## The three ways to use Axiom

### 1. One-liner: English to validated robot actions

The fastest path. Give it a task in plain English, get back validated actions:

```python
from axiom_tfg import prompt_and_resolve

result = prompt_and_resolve(
    "pick the 0.3kg sensor from the left bin and place it in the right bin",
    api_key="sk-...",           # OpenAI, Groq (free), Together, Ollama, etc.
    base_url="https://api.openai.com/v1",
    model="gpt-4o-mini",
    robot="ur5e",
)

print(result.resolved)   # True
print(result.attempts)   # 1 (or more, if the LLM needed correction)
print(result.actions)    # [{"target_xyz": [0.3, -0.3, 0.15], "mass_kg": 0.3, "is_splittable": false}, ...]
```

Works with any OpenAI-compatible API. Defaults to Groq free tier.

### 2. Bring your own VLA/planner

Wrap any action source — a VLA, a planner, a policy — in a simple callable. Axiom handles the validation loop:

```python
from axiom_tfg import resolve, Constraint

def my_vla(task: str, constraints: list[Constraint]) -> list[dict]:
    # Your model here. Use constraints to guide re-planning:
    #   constraints[-1].instruction  → "Move target within 0.85m of base"
    #   constraints[-1].proposed_patch → {"target_xyz": [0.26, 0.26, 0.26]}
    return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35, "is_splittable": False}]

result = resolve(my_vla, "pick up the mug")
if result.resolved:
    execute(result.actions)
```

Each `Constraint` carries both a human-readable `instruction` (for language models) and a structured `proposed_patch` with exact coordinates (for code-level integrations).

### 3. Direct validation

Check a single action or skip the loop entirely:

```python
from axiom_tfg import validate_action, check_simple

# Gate one action
r = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35, "is_splittable": False})
print(r.allowed)  # True

# Lower-level: full SDK check with all parameters
r = check_simple(target_xyz=[5.0, 5.0, 5.0])
print(r.verdict)              # "HARD_CANT"
print(r.reason_code)          # "NO_IK_SOLUTION"
print(r.top_fix_instruction)  # "No IK solution: ... Nearest reachable: [0.26, ...]"
```

## Supported robots

Five robots out of the box, each with real kinematic parameters and a bundled URDF:

| Robot | DOF | Reach (m) | Payload (kg) |
|-------|-----|-----------|-------------|
| `ur3e` | 6 | 0.50 | 3.0 |
| `ur5e` | 6 | 1.85 | 5.0 |
| `ur10e` | 6 | 1.30 | 12.5 |
| `franka` | 7 | 0.855 | 3.0 |
| `kuka_iiwa14` | 7 | 0.82 | 14.0 |

Pass `robot="franka"` to any function and reach, payload, URDF, link names are all set automatically. Explicit kwargs still override.

```python
from axiom_tfg import check_simple

r = check_simple(target_xyz=[0.4, 0.2, 0.3], robot="franka")
# Uses franka's 0.855m reach, 3.0kg payload, and 7-DOF URDF automatically
```

## What Axiom checks

Five gates run in sequence. First failure short-circuits.

| Gate | Question | Reason code |
|------|----------|-------------|
| **IK feasibility** | Does an IK solution exist for this pose? (URDF + ikpy, 6-seed multi-start) | `NO_IK_SOLUTION` / `ORIENTATION_MISMATCH` |
| **Reachability** | Is the target within the robot's reach sphere? (fallback when no URDF) | `OUT_OF_REACH` |
| **Payload** | Can the robot lift this mass? | `OVER_PAYLOAD` |
| **Keepout** | Is the target outside all forbidden zones? | `IN_KEEP_OUT_ZONE` |
| **Path keepout** | Does the path to the target cross any forbidden zone? | `PATH_CROSSES_KEEP_OUT` |

Every failure comes with a **counterfactual fix** — the smallest change that would make it work:

- `MOVE_TARGET` — exact coordinates to a reachable point
- `MOVE_BASE` — move the robot closer
- `SPLIT_PAYLOAD` — split into N trips with staging coordinates and per-trip mass
- `CHANGE_CONSTRUCTOR` — names specific capable robots from the registry (e.g. "Robots that can handle this: ur10e (12.5kg), kuka_iiwa14 (14.0kg)")

These fixes are what close the loop. The LLM reads the fix instruction and adjusts.

## The resolve loop

The core of Axiom. Orchestrates propose → validate → fix → re-plan:

```
resolve(vla_callable, "pick up the mug", max_retries=3)

  Attempt 0:
    VLA proposes [target: 5.0, 5.0, 5.0]     → BLOCKED (NO_IK_SOLUTION)
    Fix: "Nearest reachable: [0.26, 0.26, 0.26]"

  Attempt 1:
    VLA proposes [target: 0.26, 0.26, 0.26]   → PASS
    Resolved in 2 attempts
```

Three ways the loop ends:
- Action passes all gates → `resolved=True`
- Max retries exhausted → `resolved=False` with full history
- Gate fails with no available fix → stops early

The `ResolveResult` contains the full `history` of every attempt, every constraint, and every action — complete observability.

## LLM codegen adapter

`prompt_and_resolve()` uses `make_codegen_vla()` internally. For more control:

```python
from axiom_tfg import make_codegen_vla, resolve

# Build a reusable LLM-backed callable
vla = make_codegen_vla(
    api_key="sk-...",
    base_url="http://localhost:11434/v1",  # Ollama
    model="llama3",
    robot="ur5e",
    max_reach_m=0.85,
    max_payload_kg=5.0,
)

# Use it with resolve — or call it directly
result = resolve(vla, "stack the red blocks on the blue platform", max_retries=5)
```

Environment variables (`AXIOM_OPENAI_API_KEY`, `AXIOM_OPENAI_BASE_URL`, `AXIOM_CODEGEN_MODEL`) work as defaults so you don't have to pass keys in code.

## Architecture

```
"pick up the mug"              ← human intent (English)
       |
  LLM (frontend)               ← understands language, generates actions
       |
  TaskSpec                      ← structured intermediate representation
       |
  Axiom gates (optimizer)       ← validates physics, computes fixes
       |
  Validated actions             ← physically feasible plan
       |
  VLA / robot (backend)         ← executes motor control
```

The compiler analogy: LLM is the frontend (parses intent). Axiom is the optimizer (ensures correctness). VLA is the backend (generates machine code). Each layer does one thing well.

## CLI

### Quick demo

```bash
tfg demo --out runs/
```

### Single feasibility check

```bash
tfg run examples/pick_place_can.yaml --out out/
# CAN: all gates passed

tfg run examples/pick_place_cant_reach.yaml --out out/
# HARD_CANT: OUT_OF_REACH (exit code 1)
```

### CI-friendly commands

```bash
# Scaffold a project with examples, regressions, CI workflow
axiom init my-robot-project

# Single run with JUnit output
axiom run task.yaml --junit --out artifacts/

# Deterministic parameter sweep
axiom sweep task.yaml --n 50 --seed 1337 --mass-min 0.1 --mass-max 10.0

# Regression replay — re-run saved bundles, diff against expected verdicts
axiom replay regressions/ --out artifacts/replay
```

All commands exit 0 on success, 2 on failure. JUnit XML for CI integration.

## Web UI + API

```bash
uvicorn axiom_server.app:app --reload
# open http://localhost:8000
```

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/runs` | Submit TaskSpec, get verdict + evidence |
| `POST` | `/sweeps` | Run parameter sweep |
| `POST` | `/ai/generate` | LLM generates TaskSpec from prompt |
| `POST` | `/ai/explain` | LLM explains evidence in plain English |
| `GET` | `/runs/{id}/evidence` | Download evidence JSON |

See environment variables for configuring AI providers (Gemini, Groq, Together, OpenRouter).

## ROS 2 integration

Pre-flight proxy for Nav2. Intercepts `NavigateToPose` goals, validates against Axiom gates, and only forwards feasible goals to the real Nav2 stack.

```bash
ros2 run axiom_preflight_nav2 axiom-preflight-nav2
```

## TaskSpec YAML

The intermediate representation. You can write these by hand, or let the LLM generate them:

```yaml
task_id: pick-place-001
meta:
  template: pick_and_place

substrate:
  id: mug
  mass_kg: 0.35
  initial_pose:
    xyz: [0.3, -0.2, 0.1]

transformation:
  target_pose:
    xyz: [0.5, 0.3, 0.6]
  tolerance_m: 0.01
  target_rpy_rad: [0.0, 3.1416, 0.0]   # optional orientation

constructor:
  id: ur5e
  base_pose:
    xyz: [0.0, 0.0, 0.0]
  max_reach_m: 0.85
  max_payload_kg: 5.0
  urdf_path: robots/ur5e.urdf           # optional — enables IK gate

environment:
  keepout_zones:
    - id: safety_cage
      min_xyz: [0.3, 0.3, 0.0]
      max_xyz: [0.7, 0.7, 1.0]

allowed_adjustments:
  can_move_target: true
  can_change_constructor: true
  can_split_payload: false        # true only for divisible loads
```

## Tests

```bash
pytest -v    # 262 tests
```

## License

MIT
