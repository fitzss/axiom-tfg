# axiom-tfg

[![CI](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Deterministic physical-task feasibility gate linter.

`axiom-tfg` reads a **TaskSpec** YAML describing a robotic pick-and-place (or similar) task and runs a pipeline of deterministic **feasibility gates**. It outputs a structured **EvidencePacket** JSON with a `CAN` / `HARD_CANT` verdict, measured values, and ranked **counterfactual fixes** ("what's the smallest change to make this feasible?").

Designed to run as a CI gate — exit code 0 means feasible, exit code 1 means not.

Ships with a **web UI + REST API** (FastAPI) and a **Docker Compose** setup for one-command deployment on any VM (see [VULTR_DEPLOY.md](VULTR_DEPLOY.md)).

## Install

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

## Web UI + API server

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | No | Gemini API key. Enables the AI Assistant panel in the web UI. |
| `AXIOM_PUBLIC_BASE_URL` | No | When set, `evidence_url` in API responses becomes an absolute URL (e.g. `https://axiom.example.com`). |

Copy the example file and fill in your values:

```bash
cp .env.example .env
# edit .env with your API key
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

### Demo flow

1. Open `http://localhost:8000` in your browser.
2. Paste a TaskSpec YAML into the text area (a sample is pre-filled).
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
| `GET` | `/ai/status` | AI feature status — `{"ai_enabled": true/false}` |
| `POST` | `/ai/generate` | Generate a TaskSpec YAML from a natural-language prompt (requires `GOOGLE_API_KEY`) |
| `POST` | `/ai/explain` | Get a 1-sentence explanation of an EvidencePacket (requires `GOOGLE_API_KEY`) |
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
| **reachability** | Euclidean distance from `constructor.base_pose` to `transformation.target_pose` <= `max_reach_m` | `OUT_OF_REACH` |
| **payload** | `substrate.mass_kg` <= `constructor.max_payload_kg` | `OVER_PAYLOAD` |
| **keepout** | `transformation.target_pose` must not lie inside any `environment.keepout_zones` AABB (expanded by `safety_buffer`) | `IN_KEEP_OUT_ZONE` |

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

constructor:
  id: ur5e
  base_pose:
    xyz: [0.0, 0.0, 0.0]
  max_reach_m: 1.85
  max_payload_kg: 5.0

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
