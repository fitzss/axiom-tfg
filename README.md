# axiom-tfg

[![CI](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**Tell a robot what to do in plain English. Axiom makes sure it's physically possible.**

LLMs can generate robot code from natural language. But they don't know your robot's reach limits, payload capacity, or keepout zones — so the code fails on real hardware. Axiom is the **physics compiler** between the AI and the robot: it validates every action, and when something is infeasible, it computes the *exact fix* and feeds it back to the LLM automatically until the plan works.

The fix, not the gate, is the product. Anyone can write a reach check. Nobody else computes the smallest change that makes a failed action feasible *and* closes the loop with the LLM automatically.

```python
from axiom_tfg import prompt_and_resolve

result = prompt_and_resolve(
    "pick up the mug and put it on the shelf",
    api_key="sk-...",       # any OpenAI-compatible API
)

for action in result.actions:
    robot.move_to(action["target_xyz"])
```

English in, physically valid robot actions out.

## How the fix loop works

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

This matters because **physics checking is the difference between a demo and a working system.** Text2Motion (Lin et al., 2023) showed that adding geometric feasibility checking raised task success from 13% to 82% — a 6x improvement. Axiom goes further: instead of just rejecting bad plans, it computes the fix and closes the loop, so the LLM converges to a valid plan without human intervention.

Think of it like a compiler. The LLM is the **frontend** (parses human intent into actions). Axiom is the **optimizer** (validates physics, computes fixes). The VLA or robot is the **backend** (executes motor control). Each layer does one thing well.

```
 LLM (frontend)  →  Axiom (optimizer)  →  Robot (backend)
 parses intent       validates physics      executes motion
                     computes fixes
```

## Why not just _X_?

| System | Deterministic | Computes fixes | Closes the loop | Robot-specific |
|--------|:---:|:---:|:---:|:---:|
| Isaac Sim / PyBullet | Yes | No | No | Yes |
| MoveIt / OMPL | Yes | No | No | Yes |
| SayCan | No | No | No | Partially |
| Text2Motion | Partially | No | Partially | Yes |
| **Axiom** | **Yes** | **Yes** | **Yes** | **Yes** |

No existing system validates deterministically, computes structured fixes, *and* closes the loop with the LLM. (Details: [THE_PROBLEM.md](THE_PROBLEM.md))

## What Axiom checks

Five gates run in sequence. First failure short-circuits.

| Gate | Question | Fix |
|------|----------|-----|
| **IK feasibility** | Does an IK solution exist for this pose? | `MOVE_TARGET` — exact reachable coordinates |
| **Reachability** | Is the target within reach? | `MOVE_TARGET` or `MOVE_BASE` |
| **Payload** | Can the robot lift this mass? | `SPLIT_PAYLOAD` or `CHANGE_CONSTRUCTOR` |
| **Keepout** | Is the target in a forbidden zone? | `MOVE_TARGET` — nearest safe point |
| **Path keepout** | Does the path cross a forbidden zone? | `MOVE_TARGET` — rerouted waypoint |

Every fix carries both a human-readable instruction (for LLMs) and exact coordinates (for code).

## Supported robots

Five robots out of the box, each with real kinematic parameters and a bundled URDF:

| Robot | DOF | Reach (m) | Payload (kg) |
|-------|-----|-----------|-------------|
| `ur3e` | 6 | 0.50 | 3.0 |
| `ur5e` | 6 | 1.85 | 5.0 |
| `ur10e` | 6 | 1.30 | 12.5 |
| `franka` | 7 | 0.855 | 3.0 |
| `kuka_iiwa14` | 7 | 0.82 | 14.0 |

Pass `robot="franka"` to any function — reach, payload, URDF, and link names are all set automatically.

## Three ways to use it

### 1. One-liner: English to validated robot actions

```python
from axiom_tfg import prompt_and_resolve

result = prompt_and_resolve(
    "pick the 0.3kg sensor from the left bin and place it in the right bin",
    api_key="sk-...",           # OpenAI, Groq, Together, Ollama, etc.
    base_url="https://api.openai.com/v1",
    model="gpt-4o-mini",
    robot="ur5e",
)

print(result.resolved)   # True
print(result.attempts)   # 1 (or more, if the LLM needed correction)
print(result.actions)    # [{"target_xyz": [...], "mass_kg": 0.3, ...}]
```

### 2. Bring your own VLA/planner

Wrap any action source — a VLA, a planner, a policy — in a simple callable:

```python
from axiom_tfg import resolve, Constraint

def my_vla(task: str, constraints: list[Constraint]) -> list[dict]:
    # Your model here. constraints[-1].instruction has the fix in English.
    # constraints[-1].proposed_patch has exact coordinates.
    return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35, "is_splittable": False}]

result = resolve(my_vla, "pick up the mug")
if result.resolved:
    execute(result.actions)
```

### 3. Direct validation

Gate a single action without the loop:

```python
from axiom_tfg import validate_action, check_simple

r = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35, "is_splittable": False})
print(r.allowed)  # True

r = check_simple(target_xyz=[5.0, 5.0, 5.0])
print(r.verdict)              # "HARD_CANT"
print(r.top_fix_instruction)  # "No IK solution: ... Nearest reachable: [0.26, ...]"
```

## Install

```bash
pip install -e ".[dev]"    # from source, Python 3.11+
```

## CLI

```bash
tfg demo --out runs/                          # quick demo
tfg run examples/pick_place_can.yaml          # single feasibility check
axiom sweep task.yaml --n 50 --seed 1337      # parameter sweep
axiom replay regressions/ --out artifacts/    # regression replay
```

All commands exit 0 on pass, 2 on fail. JUnit XML output for CI with `--junit`.

## API + Web UI

```bash
uvicorn axiom_server.app:app --reload     # http://localhost:8000
```

POST to `/runs` (single check), `/sweeps` (parameter sweep), or `/ai/generate` (LLM-generated TaskSpec). See environment variables for provider config.

## ROS 2

Pre-flight proxy for Nav2 — intercepts `NavigateToPose` goals, validates against Axiom gates, forwards only feasible goals: `ros2 run axiom_preflight_nav2 axiom-preflight-nav2`

## Tests

```bash
pytest -v    # 262 tests
```

## Further reading

- **[THE_PROBLEM.md](THE_PROBLEM.md)** — why AI-to-robot pipelines fail, with 21 citations from the research literature
- **[PITCH.md](PITCH.md)** — the market opportunity and what makes Axiom defensible
- **[WHITEPAPER.md](WHITEPAPER.md)** — technical architecture of the physics grounding layer

## License

MIT
