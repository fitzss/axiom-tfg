# Axiom: Physical Constraint Validation for Robot Actions

## Problem

AI systems (LLMs, VLAs, planners) propose robot actions without knowing what is physically possible. Targets are out of reach, payloads exceed limits, paths cross forbidden zones. These failures are discovered at execution time — on real hardware, or after expensive simulation. When they fail, the system says "failed" and a human does the debugging.

Cross-robot transfer makes this worse: a trajectory collected on a Franka (0.855m reach) may be deployed on a UR3e (0.5m reach). Nobody checks systematically which parts of the data are physically valid on the target hardware.

## What Axiom does

**Validate** proposed actions against physics constraints (IK feasibility, reach, payload, keepout zones). Deterministic, sub-second, no simulator needed.

**Fix** failures by computing the smallest change that makes the action feasible — exact coordinates, not just "failed." Fixes carry both human-readable instructions (for LLMs) and machine-readable patches (for code).

**Map** the boundary between possible and impossible for a given robot. Compare feasible spaces across robots. Overlay dataset coverage to find blind spots.

## Key results

**Planner loop:** Propose action -> validate -> reject with fix -> retry -> succeed. Three verdicts: `CAN` (feasible), `CAN_WITH_PATCH` (fixed within tolerance), `HARD_CANT` (no acceptable fix). Deviation and risk are always reported.

**Robot overlap:** UR3e vs Franka at the same base position — Franka covers 100% of UR3e's feasible space, UR3e covers 7.2% of Franka's. This quantifies exactly why Franka-collected data breaks on UR3e.

**Dataset coverage:** LIBERO data on UR3e — 43% of data points are infeasible on UR3e. The data covers only 26% of UR3e's feasible space. 74% is blind spots with zero training data.

**Portability audit:** 2,758 steps from LIBERO run against UR3e — 23 steps IK-confirmed infeasible (0.8%), affecting 20% of episodes, mean patch displacement 46.5mm. Each violation has a computed nearest-feasible alternative.

## What this enables

- **Pre-deployment validation:** Check whether data or policies transfer to different hardware before you try and fail
- **Actionable failure reports:** Every failure comes with exact coordinates for the fix, not just an error message
- **Capability mapping:** Know what a robot can do, where datasets have gaps, and where robots differ — quantified, not guessed
- **CI/regression gating:** Deterministic verdicts with JUnit XML output, replayable evidence artifacts

## Technical summary

- 5 physics gates (IK, reach, payload, keepout, path keepout), short-circuit on first failure
- 5 bundled robots with URDFs (UR3e, UR5e, UR10e, Franka, KUKA iiwa14)
- Atlas: 3D IK grid sampling, robot overlap comparison, dataset coverage overlay
- Audit: trajectory analysis with EE velocity/jerk, joint limits, IK feasibility
- CLI (`axiom gate`, `axiom atlas`, `axiom audit`, `axiom run`, `axiom sweep`, `axiom replay`)
- Python SDK (`check_simple`, `validate_action`, `validate_plan`, `prompt_and_resolve`)
- 316 tests, MIT licensed, Python 3.11+
