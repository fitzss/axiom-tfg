"""CNC machine tending demo — flagship scenario for axiom-tfg.

Demonstrates the closed-loop resolve engine by simulating a UR5e tending
a CNC milling machine.  Three physics problems arise naturally:

1. Parts bin is beyond IK reach  (2.52 m from base, UR5e reach = 1.85 m)
2. CNC loading point is inside a safety cage (keepout zone)
3. Finished part is too heavy for the UR5e  (8 kg vs 5 kg payload limit)

A deterministic mock VLA proposes naive actions, and the resolve loop
converges across three different gate types in 4 attempts.

Usage::

    from axiom_tfg.demo_scenario import run_demo

    result = run_demo()          # deterministic, no API key needed
    result = run_demo(live=True) # uses real LLM (requires API key)
"""

from __future__ import annotations

from typing import Any

import typer

from axiom_tfg.resolve import Constraint, ResolveResult, resolve

# ── Workspace layout ────────────────────────────────────────────────────

TASK = (
    "Pick a steel blank from the parts bin, "
    "load it into the CNC machine, "
    "and unload the finished part to the inspection table."
)

SAFETY_CAGE: dict[str, Any] = {
    "id": "safety_cage",
    "min_xyz": [0.3, 0.3, 0.0],
    "max_xyz": [0.8, 0.8, 1.0],
}

ACTION_LABELS = [
    "Pick steel blank from parts bin",
    "Load blank into CNC machine",
    "Unload finished part to inspection table",
]


# ── Mock VLA ────────────────────────────────────────────────────────────


def mock_cnc_vla(
    task: str, constraints: list[Constraint]
) -> list[dict[str, Any]]:
    """Deterministic VLA that simulates LLM behavior for the CNC scenario.

    - Attempt 0 (no constraints): returns 3 naive actions with physics problems
    - Attempt 1+: reads ``proposed_patch`` from each constraint to fix the
      failing action
    - On payload split: reads ``split_mass_kg`` and ``staging_positions``
      from the patch, returns 4 actions (2 lighter trips)
    """
    # Start with naive actions every time, then apply accumulated fixes.
    actions: list[dict[str, Any]] = [
        {
            "target_xyz": [2.5, 0.0, 0.3],
            "mass_kg": 2.0,
            "is_splittable": False,
            "label": ACTION_LABELS[0],
        },
        {
            "target_xyz": [0.5, 0.5, 0.4],
            "mass_kg": 2.0,
            "is_splittable": False,
            "label": ACTION_LABELS[1],
        },
        {
            "target_xyz": [0.4, -0.3, 0.2],
            "mass_kg": 8.0,
            "is_splittable": True,
            "label": ACTION_LABELS[2],
        },
    ]

    # Apply accumulated fixes (each constraint fixes one action).
    for i, c in enumerate(constraints):
        patch = c.proposed_patch or {}
        if i == 0:
            # IK fix for action 0: use the gate's projected target.
            actions[0]["target_xyz"] = patch.get(
                "projected_target_xyz", actions[0]["target_xyz"]
            )
        elif i == 1:
            # Keepout fix for action 1: escape to zone boundary.
            actions[1]["target_xyz"] = patch.get(
                "projected_target_xyz", actions[1]["target_xyz"]
            )
        elif i == 2:
            # Payload split for action 2: 2 lighter trips.
            split_mass = patch.get("split_mass_kg", 4.0)
            staging = patch.get("staging_positions", [[0.3, -0.15, 0.2]])
            actions = actions[:2] + [
                {
                    "target_xyz": staging[0],
                    "mass_kg": split_mass,
                    "is_splittable": True,
                    "label": "Unload part (trip 1 \u2192 staging)",
                },
                {
                    "target_xyz": [0.4, -0.3, 0.2],
                    "mass_kg": split_mass,
                    "is_splittable": True,
                    "label": "Unload part (trip 2 \u2192 inspection table)",
                },
            ]

    return actions


# ── Narrative renderer ──────────────────────────────────────────────────


def _fmt(xyz: list[float]) -> str:
    """Format [x, y, z] for display."""
    return f"[{xyz[0]:.2f}, {xyz[1]:.2f}, {xyz[2]:.2f}]"


# Plain-English failure summaries keyed by gate reason code.
_REASON_SUMMARIES: dict[str, str] = {
    "NO_IK_SOLUTION": "Arm can't reach the target — too far from the base",
    "OUT_OF_REACH": "Target is beyond the arm's maximum reach",
    "IN_KEEP_OUT_ZONE": "Target is inside a keepout zone — forbidden region",
    "OVER_PAYLOAD": "Object is too heavy for this robot",
    "PATH_CROSSES_KEEPOUT": "Motion path passes through a keepout zone",
}

# What would happen on real hardware without validation.
_HARDWARE_CONSEQUENCES: dict[str, str] = {
    "NO_IK_SOLUTION": "arm hits joint limits, task fails",
    "OUT_OF_REACH": "arm extends fully, can't reach target, task fails",
    "IN_KEEP_OUT_ZONE": "arm enters forbidden zone — loss risk or e-stop",
    "OVER_PAYLOAD": "motor overload — joint fault or dropped part",
    "PATH_CROSSES_KEEPOUT": "arm sweeps through forbidden zone mid-motion",
}

# Plain-English fix summaries keyed by fix type.
_FIX_SUMMARIES: dict[str, str] = {
    "MOVE_TARGET": "move target to safe/reachable position",
    "MOVE_BASE": "reposition the robot base",
    "SPLIT_PAYLOAD": "split into multiple lighter trips",
    "CHANGE_CONSTRUCTOR": "use a different robot",
}


def _explain_fix(c: Constraint) -> str:
    """Build a one-line fix description from a constraint."""
    fix_label = _FIX_SUMMARIES.get(c.fix_type or "", c.fix_type or "unknown")
    patch = c.proposed_patch or {}

    if c.fix_type == "SPLIT_PAYLOAD":
        count = patch.get("suggested_payload_split_count", 2)
        mass = patch.get("split_mass_kg")
        if mass is not None:
            return f"{fix_label} — {count} trips of {mass} kg each"
    elif "projected_target_xyz" in patch:
        xyz = _fmt(patch["projected_target_xyz"])
        return f"{fix_label} → {xyz}"

    return fix_label


def _print_workspace(e: Any) -> None:
    """Print a top-down schematic of the CNC demo workspace."""
    W = 62
    bar = "\u2500" * W
    e("")
    e(f"  \u250c{bar}\u2510")
    e(f"  \u2502{'Workspace (top-down)':^{W}}\u2502")
    e(f"  \u251c{bar}\u2524")
    e(f"  \u2502{'':{W}}\u2502")
    e(f"  \u2502{'   [R] ROBOT --- reach (1.85 m) --|-- - - - > [1] PARTS BIN':{W}}\u2502")
    e(f"  \u2502{'    |                             |             2.52 m away':{W}}\u2502")
    e(f"  \u2502{'    |    +-- SAFETY CAGE ---+     |':{W}}\u2502")
    e(f"  \u2502{'    |    |  [2] CNC LOAD    |     |':{W}}\u2502")
    e(f"  \u2502{'    |    +------------------+     |':{W}}\u2502")
    e(f"  \u2502{'    |                             |':{W}}\u2502")
    e(f"  \u2502{'   [3] INSPECTION (8.0 kg)        |':{W}}\u2502")
    e(f"  \u2502{'':{W}}\u2502")
    e(f"  \u251c{bar}\u2524")
    e(f"  \u2502{' [1] Beyond reach   [2] Inside cage   [3] Too heavy (5 kg)':^{W}}\u2502")
    e(f"  \u2514{bar}\u2518")


def print_demo(result: ResolveResult, elapsed_s: float | None = None) -> None:
    """Render the resolve history as a human-readable narrative."""
    e = typer.echo

    e("")
    e("\u2554" + "\u2550" * 62 + "\u2557")
    e("\u2551" + "  CNC Machine Tending \u2014 UR5e Demo".center(62) + "\u2551")
    e("\u255a" + "\u2550" * 62 + "\u255d")

    _print_workspace(e)

    e("")
    e("  The planner generates actions from the task description alone.")
    e("  It doesn't know the robot's reach limits, safety zones, or")
    e("  payload capacity. Without validation, these actions go straight")
    e("  to hardware \u2014 and fail. Watch Axiom catch each problem, compute")
    e("  the exact fix, and feed it back until the plan is valid.")

    e("")
    e("\u2500" * 64)

    for attempt in result.history:
        n = attempt.attempt
        actions = attempt.actions
        r = attempt.result

        e(f"\n  Attempt {n}")
        e("  " + "\u2500" * 58)

        e(f"  Planner proposes {len(actions)} action(s):")
        for j, a in enumerate(actions):
            label = a.get("label", f"Action {j}")
            xyz = _fmt(a["target_xyz"])
            mass = a.get("mass_kg", "?")
            e(f"    {j + 1}. {label}")
            e(f"       target={xyz}  mass={mass} kg")

        if r.allowed:
            e(f"\n  \u2713 All gates pass \u2014 plan is physically valid!")
        else:
            c = attempt.constraint_added
            if c:
                summary = _REASON_SUMMARIES.get(c.reason, c.reason)
                consequence = _HARDWARE_CONSEQUENCES.get(c.reason, "")
                e(f"\n  \u2717 {summary}")
                if consequence:
                    e(f"    Without Axiom: {consequence}")
                e(f"    Fix: {_explain_fix(c)}")

    e("")
    e("\u2500" * 64)

    if result.resolved:
        n_failures = len(result.constraints)
        timing = f" in {elapsed_s:.1f}s" if elapsed_s is not None else ""
        e(f"\n  \u2713 Resolved in {result.attempts} attempt(s){timing}")
        e(f"    {n_failures} physics failure(s) caught and repaired:")
        for i, c in enumerate(result.constraints):
            summary = _REASON_SUMMARIES.get(c.reason, c.reason)
            fix = _FIX_SUMMARIES.get(c.fix_type or "", c.fix_type or "")
            e(f"      {i + 1}. {summary} \u2014 {fix}")
        e(f"\n  Final validated plan ({len(result.actions)} actions):")
        for j, a in enumerate(result.actions):
            label = a.get("label", f"Action {j}")
            xyz = _fmt(a["target_xyz"])
            mass = a.get("mass_kg", "?")
            e(f"    {j + 1}. {label}")
            e(f"       target={xyz}  mass={mass} kg")
        e("")
        e("  What this replaced:")
        e("    Without Axiom, each failure is discovered on the real robot \u2014")
        e("    the arm stalls, enters a safety zone, or overloads a joint.")
        e("    A human debugs, adjusts coordinates by hand, and retries.")
        e("    With Axiom, the planner gets exact fixes and converges")
        e("    automatically. No simulator, no hardware, no manual tuning.")
    else:
        e(f"\n  \u2717 Failed to resolve after {result.attempts} attempts")

    e("")


# ── Entry point ─────────────────────────────────────────────────────────


def run_demo(
    live: bool = False,
    model: str | None = None,
    base_url: str | None = None,
) -> ResolveResult:
    """Run the CNC machine tending demo.

    Args:
        live: If True, use a real LLM via ``prompt_and_resolve``
              (requires API key).  If False (default), use the
              deterministic mock VLA.
        model: LLM model name (only used when ``live=True``).
        base_url: API base URL (only used when ``live=True``).

    Returns:
        :class:`~axiom_tfg.resolve.ResolveResult`
    """
    keepout_zones = [SAFETY_CAGE]

    if live:
        from axiom_tfg.codegen import prompt_and_resolve

        return prompt_and_resolve(
            TASK,
            robot="ur5e",
            max_retries=4,
            keepout_zones=keepout_zones,
            model=model,
            base_url=base_url,
        )

    return resolve(
        mock_cnc_vla,
        TASK,
        robot="ur5e",
        max_retries=4,
        keepout_zones=keepout_zones,
    )
