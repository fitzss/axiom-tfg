#!/usr/bin/env python3
"""Interactive demo presentation for axiom-tfg.

Run:
    python scripts/present.py

Walks through the value proposition in 3 steps:
  1. Instant validation — check_simple() in < 1 second
  2. Without Axiom    — the naive plan and what goes wrong
  3. With Axiom       — watch the resolve loop fix everything
"""

from __future__ import annotations

import sys
import time

# ── Helpers ───────────────────────────────────────────────────────────────


def e(text: str = "") -> None:
    print(text)


def pause(prompt: str = "Press Enter to continue...") -> None:
    try:
        input(f"\n  \033[90m{prompt}\033[0m")
    except (EOFError, KeyboardInterrupt):
        e("\n")
        sys.exit(0)


def header(step: int, title: str) -> None:
    e("")
    e("  " + "=" * 60)
    e(f"  STEP {step}: {title}")
    e("  " + "=" * 60)


# ── Step 1: Instant validation ────────────────────────────────────────────


def step_instant_check() -> None:
    header(1, "Instant Validation")
    e("")
    e("  One function call. Sub-second. Exact fix coordinates back.")

    from axiom_tfg import check_simple

    # Warm up URDF/IK cache silently so the timed call is representative.
    e("  \033[90m(loading robot model...)\033[0m")
    check_simple(target_xyz=[0.1, 0.0, 0.1])

    e("")
    e("  \033[90m>>> from axiom_tfg import check_simple\033[0m")
    e("  \033[90m>>> r = check_simple(target_xyz=[2.5, 0.0, 0.3])\033[0m")
    e("")

    t0 = time.monotonic()
    r = check_simple(target_xyz=[2.5, 0.0, 0.3])
    dt = time.monotonic() - t0

    e(f"  verdict:           {r.verdict}")
    e(f"  failed_gate:       {r.failed_gate}")
    e(f"  top_fix:           {r.top_fix}")
    e(f"  fix_instruction:   {r.top_fix_instruction}")
    e(f"  level_reached:     {r.validation_level_reached}")
    e("")
    e(f"  \033[32mCompleted in {dt:.2f}s\033[0m")
    e("")
    e("  Not just 'no' — the exact reachable point and how to fix it.")

    pause()

    # Show a passing check for contrast.
    e("")
    e("  Now a valid action:")
    e("")
    e("  \033[90m>>> r = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=0.35)\033[0m")
    e("")

    t0 = time.monotonic()
    r2 = check_simple(target_xyz=[0.4, 0.2, 0.5], mass_kg=0.35)
    dt2 = time.monotonic() - t0

    e(f"  verdict:           {r2.verdict}")
    e(f"  level_reached:     {r2.validation_level_reached}")
    e("")
    e(f"  \033[32mCompleted in {dt2:.2f}s\033[0m")
    e("")
    e("  CAN + L0 = all endpoint feasibility checks passed.")
    e("  This is what your planner gets back before any hardware moves.")


# ── Step 2: Without Axiom ─────────────────────────────────────────────────


def step_without_axiom() -> None:
    header(2, "Without Axiom — What Goes Wrong")
    e("")
    e("  An LLM generates this plan for a UR5e tending a CNC machine:")
    e("")
    e("    Action 1: Pick from parts bin     [2.50, 0.00, 0.30]  2.0 kg")
    e("    Action 2: Load into CNC machine   [0.50, 0.50, 0.40]  2.0 kg")
    e("    Action 3: Unload to inspection    [0.40, -0.30, 0.20] 8.0 kg")
    e("")
    e("  Looks reasonable. The LLM doesn't know about the robot's limits.")
    e("  What happens if you send this plan straight to hardware?")

    pause()

    e("")
    e("    \033[31m✗ Action 1:\033[0m parts bin is 2.52 m away — arm reaches 1.85 m")
    e("      \033[90m→ arm moves toward target, hits joint limits, task fails\033[0m")
    e("")
    e("    \033[31m✗ Action 2:\033[0m CNC load point is inside the safety cage")
    e("      \033[90m→ arm enters forbidden zone — Loss risk or emergency stop\033[0m")
    e("")
    e("    \033[31m✗ Action 3:\033[0m finished part weighs 8.0 kg — limit is 5.0 kg")
    e("      \033[90m→ motor overload — joint fault or dropped part\033[0m")
    e("")
    e("  3 out of 3 actions would fail on real hardware.")
    e("  Without validation, you discover this one crash at a time.")


# ── Step 3: With Axiom ────────────────────────────────────────────────────


def step_with_axiom() -> None:
    header(3, "With Axiom — Watch It Fix Everything")
    e("")
    e("  Same scenario. Now Axiom validates each plan, computes the")
    e("  exact fix, feeds it back to the planner, and loops until valid.")

    pause()

    from axiom_tfg.demo_scenario import print_demo, run_demo

    t0 = time.monotonic()
    result = run_demo()
    elapsed = time.monotonic() - t0

    print_demo(result, elapsed_s=elapsed)


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    e("")
    e("  \033[1maxiom-tfg\033[0m")
    e("  Physics validation for AI-generated robot actions.")
    e("")
    e("  This demo shows three things:")
    e("    1. Instant validation — one function call, sub-second")
    e("    2. Without Axiom     — what goes wrong on real hardware")
    e("    3. With Axiom        — automatic fix loop, zero crashes")

    pause("Press Enter to start...")

    step_instant_check()
    pause()
    step_without_axiom()
    pause()
    step_with_axiom()

    e("")
    e("  " + "=" * 60)
    e("")
    e("  That's axiom-tfg.")
    e("")
    e("  pip install -e '.[dev]'")
    e("  python scripts/present.py    # this demo")
    e("  tfg demo-factory             # just the resolve loop")
    e("  tfg demo-factory --live -m gpt-4o-mini  # with a real LLM")
    e("")


if __name__ == "__main__":
    main()
