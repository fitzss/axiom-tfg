"""A/B stress test: structured fixes vs. blind retries.

Runs the same 20 prompts through two modes:
  A) WITH fixes — full counterfactual fixes with coordinates fed back to LLM
  B) WITHOUT fixes — LLM only told "validation failed, try again" (no coordinates)

This is the controlled experiment that answers: do structured counterfactual
fixes actually help, or would the LLM self-correct anyway on retry?

Usage:
    export AXIOM_OPENAI_API_KEY="your-groq-key"
    python3 stress_test_loop.py

What to look for:
    - Resolve rate: A vs B
    - Avg attempts: A vs B
    - Which prompts only resolve WITH fixes?
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from axiom_tfg.codegen import make_codegen_vla, _build_messages, _call_llm, _parse_actions
from axiom_tfg.resolve import Constraint, ResolveResult, resolve
from axiom_tfg.robots import ROBOT_REGISTRY

# ── Test prompts — deliberately varied to stress different failure modes ──

PROMPTS = [
    # Should work first try (within reach, light object)
    ("ur5e", "pick up the small sensor and place it on the table nearby"),
    ("franka", "move the cup from the left to the right side of the desk"),

    # Should trigger reach fix (targets too far)
    ("ur5e", "pick up the box from the far shelf at [3.0, 2.0, 1.5]"),
    ("franka", "grab the tool from 2 metres away and bring it to the workbench"),
    ("ur3e", "pick the part from [1.0, 1.0, 0.5] and place it at [0.5, 0.5, 0.3]"),

    # Should trigger payload fix (heavy objects)
    ("ur3e", "pick up the 10kg motor and place it on the pallet"),
    ("franka", "lift the 8kg steel plate onto the shelf"),

    # Mixed: multiple potential failures
    ("kuka_iiwa14", "pick the 12kg block from [0.5, 0.3, 0.2] and stack it at [0.7, 0.0, 0.4]"),
    ("ur5e", "move the 0.5kg widget from the conveyor at [1.5, 0.5, 0.3] to the bin at [1.0, -0.5, 0.2]"),
    ("ur10e", "pick up the 10kg box from [1.0, 0.0, 0.5] and place it at [0.8, 0.5, 0.3]"),

    # Vague prompts — see what the LLM generates
    ("ur5e", "pick up the red thing"),
    ("franka", "stack the blocks"),
    ("ur5e", "sort the parts into bins"),

    # Complex multi-step
    ("ur5e", "pick the bottle from the left side, move it to the center, then place it on the right"),
    ("kuka_iiwa14", "pick up three screws one by one and drop them in the tray"),

    # Keepout zone scenario
    ("ur5e", "pick the mug from behind the safety barrier"),

    # Edge cases
    ("ur5e", "place the object exactly at the robot's base position [0, 0, 0]"),
    ("franka", "reach as far as possible and place the sensor"),
    ("ur5e", "pick up a 0.01kg feather and place it gently on the scale at [0.3, 0.2, 0.15]"),
    ("ur10e", "move the pallet of parts from one end of the table to the other"),
]


# ── Baseline VLA: strips fixes, only says "failed" ─────────────────────────


_BLIND_CONSTRAINT_ADDENDUM = """
IMPORTANT — your previous plan was REJECTED because it violated physical constraints.

Violations:
{constraints}

Generate a corrected JSON array.  All targets must be within the robot's reach and payload limits.
"""


def make_blind_vla(
    *,
    robot: str = "ur5e",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.2,
):
    """Like make_codegen_vla but strips coordinate fixes from constraints.

    The LLM is told which gate failed (e.g. "OUT_OF_REACH") but NOT given
    the projected reachable coordinates.  It must figure out a valid plan
    on its own.
    """
    profile = ROBOT_REGISTRY.get(robot)
    resolved_reach = profile.max_reach_m if profile else 0.85
    resolved_payload = profile.max_payload_kg if profile else 5.0

    resolved_key = api_key or os.environ.get("AXIOM_OPENAI_API_KEY", "")
    resolved_url = base_url or os.environ.get(
        "AXIOM_OPENAI_BASE_URL", "https://api.groq.com/openai/v1"
    )
    resolved_model = model or os.environ.get(
        "AXIOM_CODEGEN_MODEL", "llama-3.3-70b-versatile"
    )

    def vla(task: str, constraints: list[Constraint]) -> list[dict[str, Any]]:
        # Build the same system prompt as the real VLA
        messages = _build_messages(
            task,
            [],  # pass empty constraints — we'll add our own stripped version
            robot,
            resolved_reach,
            resolved_payload,
        )

        # If there are constraints, add a stripped version (no coordinates)
        if constraints:
            lines: list[str] = []
            for i, c in enumerate(constraints, 1):
                # Only gate name + reason — NO proposed_patch, NO coordinates
                lines.append(f"  {i}. [{c.reason}] Validation failed.")
            blind_addendum = _BLIND_CONSTRAINT_ADDENDUM.format(
                constraints="\n".join(lines)
            )
            messages[-1]["content"] = task + blind_addendum

        raw = _call_llm(
            messages,
            model=resolved_model,
            api_key=resolved_key,
            base_url=resolved_url,
            temperature=temperature,
        )
        return _parse_actions(raw)

    return vla


# ── Test runner ─────────────────────────────────────────────────────────────


def run_test(robot: str, prompt: str, idx: int, *, blind: bool = False) -> dict:
    """Run a single prompt and capture everything."""
    profile = ROBOT_REGISTRY[robot]
    mode = "BLIND" if blind else "FIXES"
    print(f"\n{'='*70}")
    print(f"[{mode}] Test {idx+1}/{len(PROMPTS)}")
    print(f"Robot: {robot} (reach={profile.max_reach_m}m, payload={profile.max_payload_kg}kg)")
    print(f"Prompt: {prompt}")
    print(f"{'='*70}")

    t0 = time.time()
    try:
        if blind:
            vla = make_blind_vla(robot=robot)
            result = resolve(
                vla, prompt, robot=robot, max_retries=3,
                max_reach_m=profile.max_reach_m,
                max_payload_kg=profile.max_payload_kg,
            )
        else:
            from axiom_tfg import prompt_and_resolve
            result = prompt_and_resolve(prompt, robot=robot, max_retries=3)

        elapsed = time.time() - t0

        print(f"\nResult: {'RESOLVED' if result.resolved else 'FAILED'}")
        print(f"Attempts: {result.attempts}")
        print(f"Time: {elapsed:.1f}s")

        if result.actions:
            for i, action in enumerate(result.actions):
                print(f"  Action {i}: target={action.get('target_xyz', '?')}, "
                      f"mass={action.get('mass_kg', '?')}kg")

        if result.constraints:
            print(f"Constraints ({len(result.constraints)}):")
            for c in result.constraints:
                print(f"  [{c.reason}] {c.instruction[:80]}")

        # Check if LLM actually used the fix (only meaningful for non-blind)
        fix_used = "n/a" if blind else "unknown"
        if not blind and len(result.history) >= 2 and result.constraints:
            last_fix = result.constraints[-1]
            if last_fix.proposed_patch and "projected_target_xyz" in last_fix.proposed_patch:
                suggested = last_fix.proposed_patch["projected_target_xyz"]
                final_actions = result.history[-1].actions
                if final_actions:
                    final_target = final_actions[0].get("target_xyz", [])
                    if final_target:
                        dist = sum((a - b) ** 2 for a, b in zip(suggested, final_target)) ** 0.5
                        fix_used = f"dist={dist:.3f}m"
                        if dist < 0.1:
                            fix_used += " (USED)"
                        elif dist < 0.5:
                            fix_used += " (PARTIAL)"
                        else:
                            fix_used += " (IGNORED)"

        return {
            "idx": idx,
            "robot": robot,
            "prompt": prompt,
            "mode": "blind" if blind else "fixes",
            "resolved": result.resolved,
            "attempts": result.attempts,
            "elapsed_s": round(elapsed, 1),
            "n_actions": len(result.actions),
            "n_constraints": len(result.constraints),
            "fix_adoption": fix_used,
            "error": None,
        }

    except Exception as e:
        elapsed = time.time() - t0
        print(f"\nERROR: {type(e).__name__}: {e}")
        return {
            "idx": idx,
            "robot": robot,
            "prompt": prompt,
            "mode": "blind" if blind else "fixes",
            "resolved": None,
            "attempts": 0,
            "elapsed_s": round(elapsed, 1),
            "n_actions": 0,
            "n_constraints": 0,
            "fix_adoption": "error",
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


def print_summary(label: str, results: list[dict]) -> dict:
    """Print summary for one run and return stats."""
    total = len(results)
    resolved = sum(1 for r in results if r["resolved"] is True)
    failed = sum(1 for r in results if r["resolved"] is False)
    errors = sum(1 for r in results if r["resolved"] is None)

    resolved_attempts = [r["attempts"] for r in results if r["resolved"]]
    avg_attempts = sum(resolved_attempts) / max(len(resolved_attempts), 1)
    avg_time = sum(r["elapsed_s"] for r in results) / total

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  Resolved:     {resolved}/{total}")
    print(f"  Failed:       {failed}/{total}")
    print(f"  Errors:       {errors}/{total}")
    print(f"  Avg attempts: {avg_attempts:.1f}")
    print(f"  Avg time:     {avg_time:.1f}s")

    if label == "WITH FIXES":
        fix_used = sum(1 for r in results if "USED" in r.get("fix_adoption", ""))
        fix_ignored = sum(1 for r in results if "IGNORED" in r.get("fix_adoption", ""))
        print(f"  Fix used:     {fix_used}")
        print(f"  Fix ignored:  {fix_ignored}")

    return {
        "resolved": resolved,
        "failed": failed,
        "errors": errors,
        "avg_attempts": round(avg_attempts, 1),
        "avg_time": round(avg_time, 1),
    }


def main():
    if not os.environ.get("AXIOM_OPENAI_API_KEY"):
        print("Set AXIOM_OPENAI_API_KEY first.")
        print("  Groq (free): https://console.groq.com/keys")
        print("  export AXIOM_OPENAI_API_KEY='gsk_...'")
        sys.exit(1)

    # ── Run A: WITH structured fixes ──
    print("\n" + "█" * 70)
    print("  PHASE A: WITH STRUCTURED FIXES (full counterfactual coordinates)")
    print("█" * 70)
    results_fixes = []
    for i, (robot, prompt) in enumerate(PROMPTS):
        results_fixes.append(run_test(robot, prompt, i, blind=False))

    # ── Run B: WITHOUT fixes (blind retries) ──
    print("\n" + "█" * 70)
    print("  PHASE B: WITHOUT FIXES (blind retries — only told 'failed')")
    print("█" * 70)
    results_blind = []
    for i, (robot, prompt) in enumerate(PROMPTS):
        results_blind.append(run_test(robot, prompt, i, blind=True))

    # ── Comparison ──
    print("\n\n" + "█" * 70)
    print("  A/B COMPARISON")
    print("█" * 70)

    stats_a = print_summary("WITH FIXES", results_fixes)
    stats_b = print_summary("WITHOUT FIXES (blind)", results_blind)

    # ── Head-to-head table ──
    print(f"\n{'─'*90}")
    print(f"{'#':>3} {'Robot':>12} {'Prompt':<42} {'Fixes':>7} {'Blind':>7} {'Delta':>7}")
    print(f"{'─'*90}")
    for rf, rb in zip(results_fixes, results_blind):
        def status(r):
            if r["resolved"] is True:
                return f"Y({r['attempts']})"
            elif r["resolved"] is False:
                return f"N({r['attempts']})"
            else:
                return "ERR"

        sf = status(rf)
        sb = status(rb)

        # Delta: + means fixes helped, - means blind was better, = means same
        if rf["resolved"] and not rb["resolved"]:
            delta = "+ FIX"
        elif not rf["resolved"] and rb["resolved"]:
            delta = "- BLIND"
        elif rf["resolved"] and rb["resolved"]:
            att_diff = rb["attempts"] - rf["attempts"]
            if att_diff > 0:
                delta = f"+{att_diff} att"
            elif att_diff < 0:
                delta = f"{att_diff} att"
            else:
                delta = "="
        else:
            delta = "="

        print(f"{rf['idx']+1:>3} {rf['robot']:>12} {rf['prompt'][:42]:<42} {sf:>7} {sb:>7} {delta:>7}")

    # ── Bottom line ──
    fix_only = sum(
        1 for rf, rb in zip(results_fixes, results_blind)
        if rf["resolved"] and not rb["resolved"]
    )
    blind_only = sum(
        1 for rf, rb in zip(results_fixes, results_blind)
        if not rf["resolved"] and rb["resolved"]
    )
    both = sum(
        1 for rf, rb in zip(results_fixes, results_blind)
        if rf["resolved"] and rb["resolved"]
    )
    neither = sum(
        1 for rf, rb in zip(results_fixes, results_blind)
        if not rf["resolved"] and not rb["resolved"]
    )
    fewer_attempts = sum(
        1 for rf, rb in zip(results_fixes, results_blind)
        if rf["resolved"] and rb["resolved"] and rf["attempts"] < rb["attempts"]
    )

    print(f"\n{'='*70}")
    print(f"  VERDICT")
    print(f"{'='*70}")
    print(f"  Resolved WITH fixes:          {stats_a['resolved']}/{len(PROMPTS)}")
    print(f"  Resolved WITHOUT fixes:       {stats_b['resolved']}/{len(PROMPTS)}")
    print(f"  Only resolved WITH fixes:     {fix_only}")
    print(f"  Only resolved WITHOUT fixes:  {blind_only}")
    print(f"  Resolved both ways:           {both}")
    print(f"  Fewer attempts WITH fixes:    {fewer_attempts}/{both} (of those resolved both ways)")
    print(f"  Neither resolved:             {neither}")

    if stats_a["resolved"] > stats_b["resolved"]:
        diff = stats_a["resolved"] - stats_b["resolved"]
        print(f"\n  >> Structured fixes resolved {diff} more prompts than blind retries.")
    elif stats_a["resolved"] == stats_b["resolved"]:
        print(f"\n  >> Same resolve rate — check attempt counts for efficiency difference.")
    else:
        print(f"\n  >> Blind retries resolved more — investigate.")

    # ── Save results ──
    out = {
        "with_fixes": results_fixes,
        "without_fixes": results_blind,
        "summary": {
            "with_fixes": stats_a,
            "without_fixes": stats_b,
            "fix_only_resolved": fix_only,
            "blind_only_resolved": blind_only,
            "both_resolved": both,
            "neither_resolved": neither,
            "fewer_attempts_with_fixes": fewer_attempts,
        },
    }
    outfile = "stress_test_results.json"
    with open(outfile, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n  Results saved to {outfile}")


if __name__ == "__main__":
    main()
