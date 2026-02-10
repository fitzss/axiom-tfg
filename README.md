# axiom-tfg

[![CI](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/axiom-tfg/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Deterministic physical-task feasibility gate linter.

`axiom-tfg` reads a **TaskSpec** YAML describing a robotic pick-and-place (or similar) task and runs a pipeline of deterministic **feasibility gates**. It outputs a structured **EvidencePacket** JSON with a `CAN` / `HARD_CANT` verdict, measured values, and ranked **counterfactual fixes** ("what's the smallest change to make this feasible?").

Designed to run as a CI gate — exit code 0 means feasible, exit code 1 means not.

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

Runs all three example YAMLs, prints a summary table, and writes evidence JSON for each.

## Gates

Gates run in order; the first failure short-circuits.

| Gate | Checks | Reason code |
|------|--------|-------------|
| **reachability** | Euclidean distance from `constructor.base_pose` to `transformation.target_pose` <= `max_reach_m` | `OUT_OF_REACH` |
| **payload** | `substrate.mass_kg` <= `constructor.max_payload_kg` | `OVER_PAYLOAD` |

## Counterfactual fixes

When a gate fails, the linter proposes ranked minimal-change fixes based on `allowed_adjustments`:

- **MOVE_TARGET** — project target onto the reach sphere (reachability)
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

## Tests

```bash
pytest -v
```

## License

MIT
