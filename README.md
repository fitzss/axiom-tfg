# axiom-tfg

[![CI](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Deterministic physical-task feasibility gate linter.

`axiom-tfg` reads a **TaskSpec** YAML describing a robotic pick-and-place (or similar) task and runs a pipeline of deterministic **feasibility gates**. It outputs a structured **EvidencePacket** JSON with a `CAN` / `HARD_CANT` verdict, measured values, and ranked **counterfactual fixes** ("what's the smallest change to make this feasible?").

Designed to run as a CI gate — exit code 0 means feasible, exit code 1 means not.

Ships with a **web UI + REST API** (FastAPI) and a **Docker Compose** setup for one-command deployment on any VM (see [VULTR_DEPLOY.md](VULTR_DEPLOY.md)).

## Getting started in 5 minutes

```bash
# 1. Install
pip install axiom-tfg

# 2. Scaffold a project
axiom init my-robot-project
cd my-robot-project

# 3. Run a feasibility check
axiom run tasks/pick_place_can.yaml --out artifacts/demo --junit
# VERDICT=CAN gate=- reason=- out=artifacts/demo

# 4. Run regression replay
axiom replay regressions/ --out artifacts/replay
# REPLAY total=3 passed=3 failed=0 out=artifacts/replay

# 5. Try the Makefile
make axiom-demo
make axiom-ci
```

That's it. You now have:
- `tasks/` — TaskSpec YAMLs defining robot tasks
- `regressions/` — artifact bundles for regression replay
- `.github/workflows/axiom.yml` — CI that runs `axiom replay` on every push
- `Makefile` — convenience targets

## Use as a Python library

```python
from axiom_tfg import check_simple
import math

# Quick position-only check (uses bundled UR5e URDF automatically)
result = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=0.35)
print(result.verdict)  # "CAN"

# 6-DOF check: position + orientation (top-down grasp)
result = check_simple(
    target_xyz=[0.4, 0.2, 0.5],
    target_rpy_rad=[0.0, math.pi, 0.0],  # EE pointing down
    mass_kg=2.0,
)
print(result.verdict)          # "CAN"
print(result.failed_gate)      # None

# Infeasible task — get the counterfactual fix
result = check_simple(target_xyz=[5.0, 5.0, 5.0])
print(result.verdict)           # "HARD_CANT"
print(result.reason_code)       # "NO_IK_SOLUTION"
print(result.top_fix_instruction)  # "No IK solution: ... Move target to ..."
```

For full control, use `check()` with a `TaskSpec`:

```python
from axiom_tfg import check
from axiom_tfg.models import TaskSpec

spec = TaskSpec.model_validate(your_yaml_dict)
result = check(spec)
```

## Install (from source)

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
```

## Quick demo

Run all bundled examples in one shot:

```bash
tfg demo --out runs/
```

Output:

```
  FILE                                VERDICT      FAILED_GATE     TOP_FIX
  ─────────────────────────────────── ──────────── ─────────────── ────────────────────
  pick_place_can.yaml                 CAN          -               -
  pick_place_cant_reach.yaml          HARD_CANT    reachability    MOVE_TARGET
  pick_place_cant_payload.yaml        HARD_CANT    payload         SPLIT_PAYLOAD
  pick_place_cant_keepout.yaml        HARD_CANT    keepout         MOVE_TARGET

Evidence written to: runs/
```

Evidence packets are written to `runs/<task_id>/evidence.json`.

## CLI usage

### `tfg run` — run feasibility gates

```bash
tfg run examples/pick_place_can.yaml --out out/
# CAN: all gates passed
# Evidence: out/pick-place-001/evidence.json

tfg run examples/pick_place_cant_reach.yaml --out out/
# HARD_CANT: OUT_OF_REACH — Move target 2.3926 m closer to base (projected onto reach sphere).
# Evidence: out/pick-place-002-reach/evidence.json
# (exit code 1)
```

### `tfg validate` — schema-only check

```bash
tfg validate examples/pick_place_can.yaml
# OK
```

### `tfg demo` — run bundled examples

```bash
tfg demo --out runs/
```

Runs all four example YAMLs, prints a summary table, and writes evidence JSON for each.

### `axiom` CLI — CI-friendly commands

The `axiom` entry-point provides `init`, `run`, `sweep`, and `replay` with artifact bundles and JUnit output for CI integration.

### `axiom init` — scaffold a project

```bash
axiom init                    # scaffold in current directory
axiom init my-project         # scaffold in a new directory
axiom init my-project --force # overwrite existing files
```

Creates: `axiom_profiles/`, `tasks/` (3 examples), `regressions/` (pre-built artifact bundles), `.github/workflows/axiom.yml`, and a `Makefile`.

```bash
# Single run with JUnit output
axiom run examples/pick_place_can.yaml --junit --out artifacts/bundle1

# Deterministic parameter sweep (junit.xml written by default)
axiom sweep examples/pick_place_can.yaml --n 50 --seed 1337 \
  --mass-min 0.1 --mass-max 10.0 --out artifacts/sweep1

# Regression replay (junit.xml written by default; use --no-junit to skip)
axiom replay artifacts/ --out artifacts/replay1
```

All three commands exit 0 on success and 2 on failure (HARD_CANT or regression mismatch). The `--junit/--no-junit` flag controls JUnit XML generation on `sweep` and `replay` (default: on); `run` uses `--junit` (default: off).

## Web UI + API server

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | No | Gemini API key. Enables the AI Assistant panel (when provider is `gemini`). |
| `AXIOM_PUBLIC_BASE_URL` | No | Makes `evidence_url` in API responses absolute (e.g. `https://axiom.example.com`). |
| `AXIOM_AI_PROVIDER` | No | `"gemini"` (default), `"openai"` (Groq/Together/OpenRouter), or `"none"`. |
| `AXIOM_GEMINI_MODEL_DEFAULT` | No | Default Gemini model (default: `gemini-2.0-flash`). |
| `AXIOM_GEMINI_MODELS_ALLOWLIST` | No | Comma-separated Gemini model allowlist. |
| `AXIOM_OPENAI_BASE_URL` | No | OpenAI-compatible API base URL (default: `https://api.groq.com/openai/v1`). |
| `AXIOM_OPENAI_API_KEY` | No | API key for the OpenAI-compatible provider (e.g. Groq API key). |
| `AXIOM_OPENAI_MODEL_DEFAULT` | No | Default model for the OpenAI provider (default: `llama-3.3-70b-versatile`). |
| `AXIOM_OPENAI_MODELS_ALLOWLIST` | No | Comma-separated model allowlist for the OpenAI provider. |
| `AXIOM_AI_DEMO_FALLBACK` | No | `"true"` for demo-proof mode: local fallback when the upstream provider is unavailable or hits quota. |

Copy the example file and fill in your values:

```bash
cp .env.example .env
# edit .env — for a demo-proof setup, just set AXIOM_AI_DEMO_FALLBACK=true
```

**Using Groq (free):** Get a free API key at [console.groq.com](https://console.groq.com), then:

```bash
AXIOM_AI_PROVIDER=openai
AXIOM_OPENAI_API_KEY=gsk_...        # your Groq key
# base URL defaults to Groq; override for Together, OpenRouter, etc.
```

### Run locally

```bash
pip install -e ".[dev]"
source .env  # optional, for AI features
uvicorn axiom_server.app:app --reload
# open http://localhost:8000
```

### Run with Docker Compose

```bash
cp .env.example .env   # edit with your values
docker compose up -d --build
# open http://localhost:8000
```

### Judge demo flow

1. Open `http://localhost:8000`.
2. Type a task in the prompt box, e.g. "Pick a 2kg box from [1,0,1] to [2,1,0.5] with a UR5e".
3. Click **Generate + Run** (or Ctrl+Enter).
4. See verdict, summary cards, and counterfactual fix.
5. Click **Apply fix to YAML**, then **Run gates** again to confirm `CAN`.
6. Expand **Inspect YAML / Advanced** for raw YAML, Task Builder, or Sweep.

### Manual demo flow

1. Open `http://localhost:8000` in your browser.
2. Expand **Inspect YAML / Advanced**, paste a TaskSpec YAML (a sample is pre-filled).
3. Click **Run gates**.
4. See the verdict (`CAN` / `HARD_CANT`), failed gate, top fix, and full evidence JSON.
5. The **Recent runs** table below updates with every run — click **evidence** to view the raw JSON.

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `POST` | `/runs` | Submit a TaskSpec (YAML `text/plain` or JSON). Returns verdict + evidence. |
| `GET` | `/runs` | List recent runs (default limit 50) |
| `GET` | `/runs/{run_id}` | Get metadata for a single run |
| `GET` | `/runs/{run_id}/evidence` | Download the evidence.json file |
| `GET` | `/examples` | List bundled example YAML filenames |
| `GET` | `/examples/{name}` | Get raw YAML text for a bundled example |
| `GET` | `/ai/status` | AI status — provider, model, fallback state |
| `GET` | `/ai/models` | List allowed models + default + current provider |
| `POST` | `/ai/generate` | Generate a TaskSpec YAML from a natural-language prompt (requires an AI provider key or fallback) |
| `POST` | `/ai/explain` | Get a 1-sentence explanation of an EvidencePacket (requires an AI provider key or fallback) |
| `POST` | `/sweeps` | Run a Scenario Sweep — generate variants and run all gates (see below) |
| `GET` | `/sweeps/{sweep_id}` | Get saved sweep summary + run IDs |
| `GET` | `/` | Web UI |

```bash
# Example: submit via curl
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: text/plain" \
  -d @examples/pick_place_can.yaml | python3 -m json.tool
```

## Gates

Gates run in order; the first failure short-circuits.

| Gate | Checks | Reason code |
|------|--------|-------------|
| **ik_feasibility** | URDF-based inverse kinematics — does a joint solution exist for the target pose? (requires `constructor.urdf_path`) | `NO_IK_SOLUTION` |
| **reachability** | Euclidean distance from `constructor.base_pose` to `transformation.target_pose` <= `max_reach_m` (fallback when no URDF) | `OUT_OF_REACH` |
| **payload** | `substrate.mass_kg` <= `constructor.max_payload_kg` | `OVER_PAYLOAD` |
| **keepout** | `transformation.target_pose` must not lie inside any `environment.keepout_zones` AABB (expanded by `safety_buffer`) | `IN_KEEP_OUT_ZONE` |

### IK feasibility gate (URDF-based reachability)

When `constructor.urdf_path` is set, Axiom loads the URDF with [ikpy](https://github.com/Phylliade/ikpy), solves inverse kinematics for the target pose, and checks whether the solution is within tolerance. This replaces the simpler spherical reachability check.

**6-DOF oriented IK:** When `transformation.target_rpy_rad` or `target_quat_wxyz` is provided, the gate checks both position AND orientation feasibility. The solver verifies the end-effector can reach the target point in the required approach direction (e.g. top-down grasp, side insertion).

**Multi-start:** To reduce false negatives from local-optimiser traps, the gate runs 6 deterministic initial seeds spaced across the joint range and keeps the best solution. All seeds are deterministic — same inputs always produce the same evidence.

If the IK gate passes, the spherical reachability gate is skipped (IK subsumes it). If no URDF is provided, the spherical check runs as the fallback — all existing TaskSpecs work unchanged.

**Constructor fields for IK:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `urdf_path` | string | No | Path to a URDF file describing the robot |
| `base_link` | string | No | Name of the base link in the URDF |
| `ee_link` | string | No | Name of the end-effector link |

**Orientation fields** (on `transformation`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target_rpy_rad` | [r,p,y] | No | Target orientation as roll/pitch/yaw in radians |
| `target_quat_wxyz` | [w,x,y,z] | No | Target orientation as unit quaternion (takes precedence over RPY) |
| `orientation_tolerance_rad` | float | No | Angular tolerance in radians (default ~10°) |

**Reason codes:**

| Code | Meaning |
|------|---------|
| `NO_IK_SOLUTION` | No joint configuration reaches the target position (and orientation if specified) |
| `ORIENTATION_MISMATCH` | Position is reachable but the required orientation cannot be achieved |

**Evidence output** includes `solver`, `ik_success`, `attempts`, `best_position_error_m`, `best_orientation_error_rad` (when orientation specified), `fk_result_xyz`, `fk_quat_wxyz`, and `joint_solution` (when solved).

Example TaskSpec with oriented IK:

```yaml
transformation:
  target_pose:
    xyz: [1.2, 0.3, 0.8]
  tolerance_m: 0.01
  target_rpy_rad: [0.0, 3.1416, 0.0]  # EE pointing down

constructor:
  id: ur5e
  base_pose:
    xyz: [0.0, 0.0, 0.0]
  max_reach_m: 1.85
  max_payload_kg: 5.0
  urdf_path: robots/ur5e.urdf
  base_link: base_link
  ee_link: ee_link
```

## Counterfactual fixes

When a gate fails, the linter proposes ranked minimal-change fixes based on `allowed_adjustments`:

- **MOVE_TARGET** — project target onto the reach sphere (reachability) or to nearest face outside keepout AABB (keepout)
- **MOVE_BASE** — move constructor base toward target (reachability)
- **SPLIT_PAYLOAD** — split into `ceil(mass / max_payload)` trips (payload)
- **CHANGE_CONSTRUCTOR** — suggest replacing the constructor (fallback)

Each fix includes a `delta`, human-readable `instruction`, and optional `proposed_patch` with concrete new values.

## TaskSpec YAML schema

```yaml
task_id: pick-place-001          # optional, auto-generated if omitted
meta:
  template: pick_and_place

substrate:
  id: soda_can
  mass_kg: 0.35
  initial_pose:
    xyz: [1.0, 0.0, 0.8]

transformation:
  target_pose:
    xyz: [1.2, 0.3, 0.8]
  tolerance_m: 0.01
  target_rpy_rad: [0.0, 3.1416, 0.0]   # optional — roll/pitch/yaw (radians)
  # target_quat_wxyz: [0, 0, 1, 0]     # alternative — quaternion [w,x,y,z]
  # orientation_tolerance_rad: 0.1745   # optional — default ~10°

constructor:
  id: ur5e
  base_pose:
    xyz: [0.0, 0.0, 0.0]
  max_reach_m: 1.85
  max_payload_kg: 5.0
  urdf_path: robots/ur5e.urdf         # optional — enables IK gate
  base_link: base_link                # optional — URDF base link name
  ee_link: ee_link                    # optional — URDF end-effector link name

environment:                          # optional
  safety_buffer: 0.02                  # metres, default 0.02
  keepout_zones:
    - id: conveyor_housing
      min_xyz: [0.3, 0.3, 0.0]
      max_xyz: [0.7, 0.7, 1.0]

allowed_adjustments:
  can_move_target: true
  can_move_base: false
  can_change_constructor: true
  can_split_payload: false
```

## EvidencePacket JSON

```json
{
  "task_id": "pick-place-001",
  "verdict": "CAN",
  "failed_gate": null,
  "checks": [
    {"gate_name": "reachability", "status": "PASS", "measured_values": {"distance_m": 1.28, "max_reach_m": 1.85}, "reason_code": null},
    {"gate_name": "payload", "status": "PASS", "measured_values": {"mass_kg": 0.35, "max_payload_kg": 5.0}, "reason_code": null}
  ],
  "counterfactual_fixes": [],
  "created_at": "2025-01-01T00:00:00+00:00",
  "axiom_tfg_version": "0.1.0"
}
```

## Scenario Sweep

Fast feasibility sweeps — not full physics simulation, but deterministic gate checks across many TaskSpec variants. Useful for mapping a robot's capability envelope.

The sweep engine samples `mass_kg` and `target_pose.xyz` uniformly within provided ranges, generates `n` variants, and runs each through the full gate pipeline. Results are deterministic for a given seed.

```bash
# Example: sweep 50 variants with mass 0.1–10 kg, target x 0.5–3.0 m
curl -s -X POST http://localhost:8000/sweeps \
  -H "Content-Type: application/json" \
  -d '{
    "base_yaml": "task_id: sweep-demo\nmeta:\n  template: pick_and_place\nsubstrate:\n  id: box\n  mass_kg: 1.0\n  initial_pose:\n    xyz: [1.0, 0.0, 0.8]\ntransformation:\n  target_pose:\n    xyz: [1.2, 0.3, 0.8]\n  tolerance_m: 0.01\nconstructor:\n  id: ur5e\n  base_pose:\n    xyz: [0.0, 0.0, 0.0]\n  max_reach_m: 1.85\n  max_payload_kg: 5.0\nallowed_adjustments:\n  can_move_target: true\n",
    "variations": {
      "mass_kg": {"min": 0.1, "max": 10.0},
      "target_xyz": {"x": {"min": 0.5, "max": 3.0}}
    },
    "n": 50,
    "seed": 1337
  }' | python3 -m json.tool
```

The response includes a summary with CAN/HARD_CANT counts, breakdown by failed gate, top reason codes, and individual run links.

## Using in CI

This repo ships a ready-made GitHub Actions workflow at `.github/workflows/ci.yml` that:

1. Runs `pytest` across Python 3.11 and 3.12
2. Executes all three example specs (the "cant" examples use `|| true` so expected failures don't break the build)
3. Uploads the `ci-runs/` evidence directory as a workflow artifact

To use axiom-tfg as a gate in your own workflow:

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with:
      python-version: "3.12"
  - run: pip install axiom-tfg
  - run: tfg run my_task.yaml --out evidence/
  # Exit code 1 = HARD_CANT, which fails the step
```

## Deployment

See [VULTR_DEPLOY.md](VULTR_DEPLOY.md) for step-by-step instructions to deploy on a Vultr VM with Docker Compose.

## Tests

```bash
pytest -v
```

## License

MIT
