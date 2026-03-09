# axiom-tfg

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**Map what a robot can do. Catch what it can't. Compute the fix.**

Axiom is a physics validation layer for robot actions. It checks proposed actions against physical constraints (kinematics, payload, keepout zones), computes the smallest change that makes a failed action feasible, and produces structured evidence artifacts for CI, regression testing, and deployment gating.

Three capabilities:

1. **Gate** — validate a proposed action, get a verdict (`CAN`, `CAN_WITH_PATCH`, `HARD_CANT`) and a counterfactual fix with exact coordinates
2. **Atlas** — map a robot's feasible transformation space, compare robots, overlay dataset coverage
3. **Audit** — analyze trajectory datasets against physical constraints, with cross-robot portability analysis

```python
from axiom_tfg import check_simple

r = check_simple(target_xyz=[0.8, 0.3, 0.2], robot="ur3e", base_xyz=[0, 0, 0.91])
print(r.verdict)              # "HARD_CANT"
print(r.reason_code)          # "NO_IK_SOLUTION"
print(r.top_fix_instruction)  # "No IK solution: ... Nearest reachable: [0.55, 0.21, 1.10]"
```

## How it works

```
Planner/LLM/VLA proposes action
        |
        v
  Axiom validates against physics gates
        |
    +---+---+
    |       |
   CAN    HARD_CANT
    |       |
    v       v
 Execute  Compute counterfactual fix
            |
            v
        Feed fix back to planner
            |
            v
        Planner adjusts and retries
```

Verdicts are semantically honest:

| Verdict | Meaning |
|---------|---------|
| `CAN` | Target is feasible as proposed |
| `CAN_WITH_PATCH` | Feasible after adjustment within tolerance |
| `HARD_CANT` | Infeasible, no acceptable fix within tolerance |

When `--auto-fix` accepts a patch, it reports the deviation and risk level. A 262mm patch is `HARD_CANT` with `intent_risk: HIGH` at default 50mm tolerance — the system does not pretend that moving the target 26cm still achieves the original task.

## Quick start

```bash
pip install -e ".[dev]"

# Gate a single action
axiom gate 0.8,0.3,1.11 --robot ur3e --base-z 0.91
axiom gate 0.8,0.3,1.11 --robot ur3e --base-z 0.91 --auto-fix
axiom gate 0.8,0.3,1.11 --robot ur3e --base-z 0.91 --auto-fix --json

# Map feasible space
axiom atlas ur3e --base-z 0.91 --resolution 0.15
axiom atlas ur3e --compare franka --base-z 0.91

# Audit a dataset
axiom audit lerobot/libero_10 --robot ur3e --base-z 0.91 --hz 10 -n 10

# Portability analysis
axiom audit lerobot/libero_10 --robot ur3e --base-z 0.91 --hz 10 -n 10 --port-from franka
```

## Planner loop demo

```bash
python3 examples/planner_loop.py --robot ur3e --base-z 0.91
```

```
  Scenario 1: Pick cup from table
  AXIOM    CAN — Feasible as proposed. Execute.

  Scenario 2: Place cup on far shelf
  AXIOM    HARD_CANT
    Gate:   ik_feasibility
    Delta:  262.5mm (exceeds 50mm tolerance)
    Risk:   HIGH — patch too large, likely breaks task intent.

  Scenario 3: Move heavy box
  AXIOM    HARD_CANT
    Gate:   payload — 15.0 kg exceeds 3.0 kg limit.
    Planner must choose a different approach.
```

## Atlas: feasible space mapping

Map what a robot can do, compare robots, find blind spots in datasets.

```bash
# Map UR3e's feasible space
axiom atlas ur3e --base-z 0.91

# Compare UR3e vs Franka
axiom atlas ur3e --compare franka --base-z 0.91
```

```
  Overlap: ur3e vs franka
  Both feasible: 35
  Only ur3e:     0
  Only franka:   454
  Overlap:       7.2%
  franka covers 100.0% of ur3e's space
  ur3e covers 7.2% of franka's space
```

```bash
# Dataset coverage on UR3e
axiom atlas ur3e --dataset lerobot/libero_10 --base-z 0.91 -n 3
```

```
  Coverage: lerobot/libero_10 on ur3e
  Feasible voxels:     42
  Visited (feasible):  11 (26.2%)
  Data in feasible:    478 / 843 (56.7%)
  Data in infeasible:  365
```

43% of the Franka dataset is infeasible on UR3e. The data covers only 26% of what UR3e can do — 74% is blind spots.

## Audit: trajectory dataset analysis

```bash
axiom audit lerobot/libero_10 --robot ur3e --base-z 0.91 --hz 10 -n 10 --port-from franka
```

Analyzes EE positions from LeRobot datasets. Computes reach margins, EE velocity/jerk profiles, keepout zone violations, and optionally IK feasibility. With `--port-from`, runs cross-robot portability analysis with IK-confirmed violations and computed fixes.

## Physics gates

Five gates run in sequence. First failure short-circuits.

| Gate | Question | Fix |
|------|----------|-----|
| **IK feasibility** | Does an IK solution exist? | `MOVE_TARGET` — nearest reachable point |
| **Reachability** | Is the target within reach? | `MOVE_TARGET` or `MOVE_BASE` |
| **Payload** | Can the robot lift this mass? | `SPLIT_PAYLOAD` or `CHANGE_CONSTRUCTOR` |
| **Keepout** | Is the target in a forbidden zone? | `MOVE_TARGET` — nearest safe point |
| **Path keepout** | Does the path cross a forbidden zone? | `MOVE_TARGET` — rerouted waypoint |

Every fix carries both a human-readable instruction and exact coordinates.

## Supported robots

Five robots with bundled URDFs and real kinematic parameters:

| Robot | DOF | Reach (m) | Payload (kg) |
|-------|-----|-----------|-------------|
| `ur3e` | 6 | 0.50 | 3.0 |
| `ur5e` | 6 | 1.85 | 5.0 |
| `ur10e` | 6 | 1.30 | 12.5 |
| `franka` | 7 | 0.855 | 3.0 |
| `kuka_iiwa14` | 7 | 0.82 | 14.0 |

## Three ways to use it

### 1. CLI

```bash
axiom gate 0.4,0.2,1.0 --robot ur3e --base-z 0.91          # single check
axiom gate 0.4,0.2,1.0 --robot ur3e --auto-fix --json       # with fix + JSON
axiom atlas ur3e --compare franka --base-z 0.91              # space mapping
axiom audit lerobot/libero_10 --robot ur3e --hz 10 -n 5     # dataset audit
axiom run task.yaml --junit                                   # YAML check + JUnit
axiom sweep task.yaml --n 50 --seed 1337                     # parameter sweep
axiom replay regressions/ --out artifacts/                    # regression replay
```

### 2. Python SDK

```python
from axiom_tfg import check_simple, validate_action, validate_plan

# Direct check
r = check_simple(target_xyz=[0.4, 0.2, 0.5], robot="ur3e")

# VLA action gating
r = validate_action({"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35})
if r.allowed:
    robot.execute(action)

# Plan gating (fail-fast)
r = validate_plan([action1, action2, action3])

# Closed-loop with LLM
from axiom_tfg import prompt_and_resolve
result = prompt_and_resolve("pick up the mug", api_key="sk-...")
```

### 3. Atlas + Audit

```python
from axiom_tfg.atlas import sample_feasible_space, compute_overlap, compute_coverage

atlas = sample_feasible_space("ur3e", base_xyz=[0, 0, 0.91])
overlap = compute_overlap("ur3e", "franka", base_xyz=[0, 0, 0.91])
coverage = compute_coverage(atlas, ee_positions)
```

## Tests

```bash
pytest -v    # 316 tests
```

## Further reading

- **[WHITEPAPER_V3.md](WHITEPAPER_V3.md)** — technical architecture, atlas, portability results
- **[ONE_PAGER.md](ONE_PAGER.md)** — one-page summary of what this is and why it matters
- **[THE_PROBLEM.md](THE_PROBLEM.md)** — why AI-to-robot pipelines fail (21 citations)

## License

MIT
