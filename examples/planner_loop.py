#!/usr/bin/env python3
"""Planner-loop demo: LLM proposes → axiom gates → fix → retry → success.

Shows the closed-loop interaction between an AI planner and the axiom-tfg
feasibility engine with semantically honest verdicts:

    CAN            — feasible as proposed
    CAN_WITH_PATCH — feasible after adjustment within tolerance
    HARD_CANT      — infeasible, no acceptable patch

Usage::

    python3 examples/planner_loop.py
    python3 examples/planner_loop.py --robot ur3e --base-z 0.91
    python3 examples/planner_loop.py --robot ur3e --base-z 0.91 --max-deviation 0.01
"""

from __future__ import annotations

import argparse
import math

from axiom_tfg.sdk import check_simple


def gate_action(
    target_xyz: list[float],
    robot: str,
    mass_kg: float,
    base_xyz: list[float],
    max_deviation: float,
) -> dict:
    """Run feasibility check with semantic honesty."""
    result = check_simple(
        target_xyz=target_xyz,
        robot=robot,
        mass_kg=mass_kg,
        base_xyz=base_xyz,
    )

    patched_xyz = (
        result.top_fix_patch.get("projected_target_xyz")
        if result.top_fix_patch
        else None
    )

    patch_delta_m = None
    if patched_xyz:
        patch_delta_m = math.sqrt(
            sum((a - b) ** 2 for a, b in zip(target_xyz, patched_xyz))
        )

    # Determine honest verdict
    if result.verdict == "CAN":
        final_verdict = "CAN"
    elif patched_xyz and patch_delta_m is not None:
        # Verify the patch is itself feasible
        r2 = check_simple(
            target_xyz=patched_xyz,
            robot=robot,
            mass_kg=mass_kg,
            base_xyz=base_xyz,
        )
        if r2.verdict == "CAN" and patch_delta_m <= max_deviation:
            final_verdict = "CAN_WITH_PATCH"
        else:
            final_verdict = "HARD_CANT"
    else:
        final_verdict = "HARD_CANT"

    return {
        "verdict": final_verdict,
        "raw_verdict": result.verdict,
        "failed_gate": result.failed_gate,
        "reason": result.reason_code,
        "fix_instruction": result.top_fix_instruction,
        "patched_xyz": patched_xyz,
        "patch_delta_m": patch_delta_m,
    }


def fmt(xyz: list[float]) -> str:
    return f"[{xyz[0]:.4f}, {xyz[1]:.4f}, {xyz[2]:.4f}]"


def run_demo(robot: str, base_z: float, max_dev: float) -> None:
    base_xyz = [0.0, 0.0, base_z]

    scenarios = [
        {
            "name": "Pick cup from table",
            "target_xyz": [0.3, 0.2, 0.15 + base_z],
            "mass_kg": 0.3,
        },
        {
            "name": "Place cup on nearby counter",
            "target_xyz": [0.45, 0.1, 0.12 + base_z],
            "mass_kg": 0.3,
        },
        {
            "name": "Place cup on far shelf",
            "target_xyz": [0.8, 0.3, 0.2 + base_z],
            "mass_kg": 0.3,
        },
        {
            "name": "Move heavy box to corner",
            "target_xyz": [0.4, 0.1, 0.3 + base_z],
            "mass_kg": 15.0,
        },
        {
            "name": "Reach behind robot base",
            "target_xyz": [-0.6, -0.5, 0.1 + base_z],
            "mass_kg": 0.2,
        },
    ]

    print(f"\n{'='*60}")
    print(f"  PLANNER LOOP DEMO (semantically honest)")
    print(f"  Robot: {robot}  Base: {fmt(base_xyz)}")
    print(f"  Max deviation: {max_dev*1000:.0f}mm")
    print(f"{'='*60}")

    stats = {"CAN": 0, "CAN_WITH_PATCH": 0, "HARD_CANT": 0}

    for i, s in enumerate(scenarios, 1):
        name = s["name"]
        target = s["target_xyz"]
        mass = s["mass_kg"]

        print(f"\n{'─'*60}")
        print(f"  Scenario {i}: {name}")
        print(f"{'─'*60}")
        print(f"\n  PLANNER  \"{name}\"")
        print(f"    target: {fmt(target)}  mass: {mass}kg")

        r = gate_action(target, robot, mass, base_xyz, max_dev)
        stats[r["verdict"]] += 1

        if r["verdict"] == "CAN":
            print(f"\n  AXIOM    CAN")
            print(f"    Feasible as proposed. Execute.")

        elif r["verdict"] == "CAN_WITH_PATCH":
            delta_mm = r["patch_delta_m"] * 1000
            print(f"\n  AXIOM    CAN_WITH_PATCH")
            print(f"    Gate:     {r['failed_gate']}")
            print(f"    Patched:  {fmt(r['patched_xyz'])}")
            print(f"    Delta:    {delta_mm:.1f}mm (within {max_dev*1000:.0f}mm tolerance)")
            print(f"    Risk:     LOW — patch is small, likely preserves intent.")

        else:  # HARD_CANT
            print(f"\n  AXIOM    HARD_CANT")
            print(f"    Gate:   {r['failed_gate']}")
            print(f"    Reason: {r['reason']}")
            if r["patch_delta_m"] is not None:
                delta_mm = r["patch_delta_m"] * 1000
                print(f"    Nearest feasible: {fmt(r['patched_xyz'])}")
                print(f"    Delta:  {delta_mm:.1f}mm (exceeds {max_dev*1000:.0f}mm tolerance)")
                print(f"    Risk:   HIGH — patch too large, likely breaks task intent.")
            elif r["fix_instruction"]:
                print(f"    Fix:    {r['fix_instruction']}")
            print(f"    Planner must choose a different approach.")

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  CAN (as proposed):     {stats['CAN']}")
    print(f"  CAN_WITH_PATCH:        {stats['CAN_WITH_PATCH']}")
    print(f"  HARD_CANT:             {stats['HARD_CANT']}")
    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Planner loop demo")
    parser.add_argument("--robot", default="ur3e")
    parser.add_argument("--base-z", type=float, default=0.91)
    parser.add_argument("--max-deviation", type=float, default=0.05,
                        help="Max patch deviation in metres (default 50mm)")
    args = parser.parse_args()
    run_demo(args.robot, args.base_z, args.max_deviation)


if __name__ == "__main__":
    main()
