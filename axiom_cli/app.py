"""Typer CLI entry-point for the ``axiom`` command.

Commands
--------
axiom init                  — scaffold a starter Axiom project
axiom run <task.yaml>       — single feasibility run + artifact bundle
axiom sweep <base.yaml>     — deterministic parameter sweep
axiom replay <dir|manifest> — regression replay against saved artifacts
"""

from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path
from typing import Any

import typer
import yaml

from axiom_tfg.models import TaskSpec
from axiom_tfg.runner import (
    junit_from_replay,
    junit_from_runs,
    load_and_run,
    run_taskspec,
    write_artifact_bundle,
)
from axiom_server.sweep import (
    RangeSpec,
    SweepRequest,
    VariationSpec,
    XYZRangeSpec,
    build_summary,
    generate_variants,
)

app = typer.Typer(
    name="axiom",
    help="CI-friendly CLI for axiom-tfg feasibility gate linter.",
    add_completion=False,
)


# ── axiom init ──────────────────────────────────────────────────────────


@app.command()
def init(
    directory: Path = typer.Argument(
        Path("."),
        help="Target directory (defaults to current directory).",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing files."
    ),
) -> None:
    """Scaffold a starter Axiom project with profiles, tasks, regressions, CI, and Makefile."""
    from axiom_cli.init_cmd import scaffold

    root = directory.resolve()
    root.mkdir(parents=True, exist_ok=True)

    lines = scaffold(root, force=force)
    for line in lines:
        typer.echo(line)

    typer.echo(f"\nAxiom project initialised in {root}")
    typer.echo("Next steps:")
    typer.echo("  axiom run tasks/pick_place_can.yaml --out artifacts/demo --junit")
    typer.echo("  axiom replay regressions/ --out artifacts/replay")
    typer.echo("  make axiom-demo")


# ── axiom run ────────────────────────────────────────────────────────────


@app.command()
def run(
    task_yaml: Path = typer.Argument(
        ..., exists=True, readable=True, help="Path to a TaskSpec YAML/JSON file."
    ),
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        help="Output directory for the artifact bundle. Defaults to artifacts/run_<id>.",
    ),
    junit: bool = typer.Option(False, "--junit", help="Write junit.xml in the bundle."),
) -> None:
    """Run a single TaskSpec through the gate pipeline and write an artifact bundle."""
    try:
        result, packet, spec = load_and_run(task_yaml)
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)

    run_id = uuid.uuid4().hex[:12]
    if out is None:
        out = Path("artifacts") / f"run_{run_id}"

    write_artifact_bundle(spec, packet, result, out, junit=junit)

    typer.echo(
        f"VERDICT={result['verdict']} "
        f"gate={result.get('failed_gate') or '-'} "
        f"reason={result.get('reason_code') or '-'} "
        f"out={out}"
    )

    code = 0 if result["verdict"] == "CAN" else 2
    raise typer.Exit(code=code)


# ── axiom sweep ──────────────────────────────────────────────────────────


@app.command()
def sweep(
    base_yaml: Path = typer.Argument(
        ..., exists=True, readable=True, help="Path to a base TaskSpec YAML file."
    ),
    n: int = typer.Option(50, "--n", "-n", help="Number of sweep variants."),
    seed: int = typer.Option(1337, "--seed", help="Random seed for determinism."),
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        help="Output directory for sweep artifacts.",
    ),
    mass_min: float = typer.Option(None, "--mass-min", help="Min mass_kg for sweep."),
    mass_max: float = typer.Option(None, "--mass-max", help="Max mass_kg for sweep."),
    junit: bool = typer.Option(True, "--junit/--no-junit", help="Write junit.xml."),
) -> None:
    """Run a deterministic parameter sweep and write sweep artifacts."""
    try:
        with open(base_yaml, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        base_task = TaskSpec.model_validate(raw)
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)

    sweep_id = uuid.uuid4().hex[:12]
    if out is None:
        out = Path("artifacts") / "sweeps" / sweep_id

    # Build variation spec from CLI flags.
    mass_range = None
    if mass_min is not None and mass_max is not None:
        mass_range = RangeSpec(min=mass_min, max=mass_max)

    variations = VariationSpec(mass_kg=mass_range)
    sweep_req = SweepRequest(base_task=base_task, variations=variations, n=n, seed=seed)
    variants = generate_variants(base_task, sweep_req)

    all_results: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    any_cant = False

    for spec in variants:
        result, packet = run_taskspec(spec)
        all_results.append(result)

        if result["verdict"] != "CAN":
            any_cant = True

        # Build CSV row.
        row: dict[str, Any] = {
            "run_id": spec.task_id,
            "verdict": result["verdict"],
            "failed_gate": result.get("failed_gate") or "",
            "reason_code": result.get("reason_code") or "",
            "mass_kg": spec.substrate.mass_kg,
            "target_x": spec.transformation.target_pose.xyz[0],
            "target_y": spec.transformation.target_pose.xyz[1],
            "target_z": spec.transformation.target_pose.xyz[2],
        }
        csv_rows.append(row)

    summary = build_summary(all_results)

    # Write artifacts.
    out.mkdir(parents=True, exist_ok=True)

    (out / "sweep.json").write_text(
        json.dumps({"sweep_id": sweep_id, "n": n, "seed": seed, "summary": summary}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    # CSV
    fieldnames = ["run_id", "verdict", "failed_gate", "reason_code", "mass_kg", "target_x", "target_y", "target_z"]
    with open(out / "sweep.csv", "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    # JUnit
    if junit:
        junit_results = []
        for r, spec in zip(all_results, variants):
            junit_results.append({**r, "task_id": spec.task_id})
        xml = junit_from_runs(junit_results)
        (out / "junit.xml").write_text(xml, encoding="utf-8")

    can = summary["CAN"]
    cant = summary["HARD_CANT"]
    typer.echo(f"SWEEP n={n} seed={seed} CAN={can} HARD_CANT={cant} out={out}")

    code = 2 if any_cant else 0
    raise typer.Exit(code=code)


# ── axiom replay ─────────────────────────────────────────────────────────


@app.command()
def replay(
    artifacts: Path = typer.Argument(
        ..., exists=True, help="Directory of artifact bundles or a manifest file."
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="Output directory for replay report."
    ),
    junit: bool = typer.Option(True, "--junit/--no-junit", help="Write junit.xml."),
) -> None:
    """Replay saved artifact bundles and compare verdicts for regression detection."""
    try:
        bundle_dirs = _resolve_bundles(artifacts)
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)

    if not bundle_dirs:
        typer.echo("ERROR: no artifact bundles found", err=True)
        raise typer.Exit(code=1)

    replay_id = uuid.uuid4().hex[:12]
    if out is None:
        out = Path("artifacts") / f"replay_{replay_id}"

    diffs: list[dict[str, Any]] = []
    any_mismatch = False

    for bundle_dir in bundle_dirs:
        diff = _replay_one(bundle_dir)
        diffs.append(diff)
        if diff["status"] == "FAIL":
            any_mismatch = True

    # Write report.
    out.mkdir(parents=True, exist_ok=True)

    passed = sum(1 for d in diffs if d["status"] == "PASS")
    failed = sum(1 for d in diffs if d["status"] == "FAIL")
    report = {"total": len(diffs), "passed": passed, "failed": failed, "diffs": diffs}
    (out / "replay_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    if junit:
        xml = junit_from_replay(diffs)
        (out / "junit.xml").write_text(xml, encoding="utf-8")

    typer.echo(f"REPLAY total={len(diffs)} passed={passed} failed={failed} out={out}")

    code = 2 if any_mismatch else 0
    raise typer.Exit(code=code)


def _resolve_bundles(path: Path) -> list[Path]:
    """Resolve a path to a list of artifact bundle directories.

    If *path* is a file, treat it as a manifest (one path per line).
    If *path* is a directory, each subdirectory containing input.yaml is a bundle.
    """
    if path.is_file():
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return [Path(line.strip()) for line in lines if line.strip()]

    # Directory: find sub-dirs with input.yaml
    bundles = sorted(
        d for d in path.iterdir() if d.is_dir() and (d / "input.yaml").exists()
    )
    return bundles


def _replay_one(bundle_dir: Path) -> dict[str, Any]:
    """Replay one artifact bundle and compare to saved result."""
    input_yaml = bundle_dir / "input.yaml"
    saved_result_path = bundle_dir / "result.json"

    # Load saved expected values.
    saved = json.loads(saved_result_path.read_text(encoding="utf-8"))
    expected_verdict = saved.get("verdict")
    expected_gate = saved.get("failed_gate")
    expected_reason = saved.get("reason_code")

    # Rerun pipeline.
    with open(input_yaml, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    spec = TaskSpec.model_validate(raw)
    result, _packet = run_taskspec(spec)

    actual_verdict = result["verdict"]
    actual_gate = result.get("failed_gate")
    actual_reason = result.get("reason_code")

    match = (
        actual_verdict == expected_verdict
        and actual_gate == expected_gate
        and actual_reason == expected_reason
    )

    return {
        "artifact": bundle_dir.name,
        "status": "PASS" if match else "FAIL",
        "expected_verdict": expected_verdict,
        "expected_gate": expected_gate,
        "expected_reason": expected_reason,
        "actual_verdict": actual_verdict,
        "actual_gate": actual_gate,
        "actual_reason": actual_reason,
    }


# ── axiom audit ─────────────────────────────────────────────────────────


@app.command()
def audit(
    source: str = typer.Argument(
        ...,
        help="HuggingFace dataset ID (e.g. 'lerobot/libero_10') or path to a JSONL trajectory file.",
    ),
    robot: str = typer.Option("franka", "--robot", "-r", help="Robot profile name."),
    base_z: float = typer.Option(
        0.91, "--base-z", help="Robot base Z position (LIBERO default: 0.91)."
    ),
    base_x: float = typer.Option(0.0, "--base-x", help="Robot base X position."),
    base_y: float = typer.Option(0.0, "--base-y", help="Robot base Y position."),
    max_episodes: int = typer.Option(
        None, "--max-episodes", "-n", help="Limit number of episodes to analyze."
    ),
    keepout: str = typer.Option(
        None,
        "--keepout",
        "-k",
        help="Keepout zone as 'id:x0,y0,z0:x1,y1,z1' (separate multiple with ';').",
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="Output directory for audit artifacts."
    ),
    ee_slice: str = typer.Option(
        "0:3", "--ee-slice", help="Slice of state vector for EE position (e.g. '0:3')."
    ),
    action_slice: str = typer.Option(
        "0:3",
        "--action-slice",
        help="Slice of action vector for position deltas (e.g. '0:3').",
    ),
    hz: float = typer.Option(
        None, "--hz", help="Control frequency in Hz (default: robot profile default, LIBERO=10)."
    ),
    run_ik: bool = typer.Option(
        False, "--ik", help="Run IK feasibility check on worst reach-margin steps."
    ),
    port_from: str = typer.Option(
        None,
        "--port-from",
        help="Portability mode: data was collected on this robot (e.g. 'franka'), audit against --robot.",
    ),
) -> None:
    """Audit a trajectory dataset against physical constraints.

    Analyzes EE positions from a LeRobot dataset or JSONL log, computing
    reach margins, EE velocity/jerk profiles, keepout zone violations,
    and optionally IK feasibility. Writes structured evidence artifacts.
    """
    from axiom_tfg.audit import (
        AuditConfig,
        KeepoutSpec,
        audit_trajectory,
        load_lerobot_trajectory,
        write_audit_report,
    )

    # Parse keepout zones
    zones: list[KeepoutSpec] = []
    if keepout:
        for zone_str in keepout.split(";"):
            zone_str = zone_str.strip()
            if not zone_str:
                continue
            parts = zone_str.split(":")
            if len(parts) != 3:
                typer.echo(
                    f"ERROR: keepout format must be 'id:x0,y0,z0:x1,y1,z1', got '{zone_str}'",
                    err=True,
                )
                raise typer.Exit(code=1)
            zone_id = parts[0]
            try:
                min_xyz = [float(v) for v in parts[1].split(",")]
                max_xyz = [float(v) for v in parts[2].split(",")]
            except ValueError:
                typer.echo(f"ERROR: could not parse coordinates in '{zone_str}'", err=True)
                raise typer.Exit(code=1)
            zones.append(KeepoutSpec(id=zone_id, min_xyz=min_xyz, max_xyz=max_xyz))

    # Parse slices
    ee_start, ee_end = (int(x) for x in ee_slice.split(":"))

    config = AuditConfig(
        robot=robot,
        base_xyz=[base_x, base_y, base_z],
        keepout_zones=zones if zones else None,
        control_hz=hz,
    )

    # Load data
    source_path = Path(source)
    if source_path.exists() and source_path.suffix == ".jsonl":
        typer.echo(f"Loading trajectory from {source_path}...")
        act_start, act_end = (int(x) for x in action_slice.split(":"))
        data = _load_jsonl_trajectory(source_path, ee_start, ee_end, act_start, act_end)
    else:
        typer.echo(f"Loading from HuggingFace: {source}...")
        act_start, act_end = (int(x) for x in action_slice.split(":"))
        data = load_lerobot_trajectory(
            source,
            max_episodes=max_episodes,
            ee_slice=(ee_start, ee_end),
            action_pos_slice=(act_start, act_end),
        )

    typer.echo(
        f"Loaded {len(data['ee_positions'])} steps across "
        f"{len(set(data['episodes'].tolist()))} episodes"
    )

    # Run audit
    report = audit_trajectory(
        ee_positions=data["ee_positions"],
        config=config,
        actions=data["actions"],
        next_ee_positions=data["next_ee_positions"],
        episodes=data["episodes"],
        run_ik=run_ik,
    )

    # Write artifacts
    if out is None:
        out = Path("artifacts") / "audit"
    report_path = write_audit_report(report, out)

    # Print summary
    typer.echo(report.summary())

    # ── Portability report: IK-confirm violations + compute fixes ──
    if port_from:
        reach_violations = [
            f for f in report.flagged if f.flag_type == "REACH_VIOLATION"
        ]
        if reach_violations:
            from axiom_tfg.sdk import check_simple

            typer.echo(f"\n  Portability: {port_from} -> {robot}")
            typer.echo(f"  {'-' * 40}")

            # Episode-level stats
            ep_set = set(data["episodes"].tolist())
            eps_with_violation = set(f.episode for f in reach_violations)
            typer.echo(
                f"  Episodes with violations: {len(eps_with_violation)} / "
                f"{len(ep_set)} ({100 * len(eps_with_violation) / len(ep_set):.0f}%)"
            )

            # IK-confirm violations and compute fixes
            patches = []
            for v in reach_violations:
                result = check_simple(
                    target_xyz=v.ee_xyz,
                    robot=robot,
                    base_xyz=[base_x, base_y, base_z],
                    mass_kg=0.1,
                )
                patch_entry = {
                    "step": v.step,
                    "episode": v.episode,
                    "original_xyz": v.ee_xyz,
                    "verdict": result.verdict,
                    "reason": result.reason_code,
                    "fix_type": result.top_fix,
                    "fix_instruction": result.top_fix_instruction,
                    "patched_xyz": (
                        result.top_fix_patch.get("projected_target_xyz")
                        if result.top_fix_patch
                        else None
                    ),
                }
                patches.append(patch_entry)

            ik_confirmed = sum(1 for p in patches if p["verdict"] == "HARD_CANT")
            typer.echo(f"  IK-confirmed infeasible: {ik_confirmed} / {len(reach_violations)}")

            if patches:
                import numpy as np

                deltas = []
                for p in patches:
                    if p["patched_xyz"] and p["original_xyz"]:
                        d = np.linalg.norm(
                            np.array(p["patched_xyz"]) - np.array(p["original_xyz"])
                        )
                        deltas.append(d)
                if deltas:
                    typer.echo(
                        f"  Mean patch displacement: {np.mean(deltas)*1000:.1f}mm"
                    )
                    typer.echo(
                        f"  Max patch displacement:  {np.max(deltas)*1000:.1f}mm"
                    )

            # Write portability patches
            patches_path = out / "portability_patches.jsonl"
            with open(patches_path, "w", encoding="utf-8") as f:
                for p in patches:
                    f.write(json.dumps(p) + "\n")
            typer.echo(f"\n  Patches written to: {patches_path}")
        else:
            typer.echo(f"\n  Portability: {port_from} -> {robot}: all steps feasible.")

    typer.echo(f"\nArtifacts written to: {out}/")

    code = 2 if report.flagged else 0
    raise typer.Exit(code=code)


def _load_jsonl_trajectory(
    path: Path,
    ee_start: int,
    ee_end: int,
    act_start: int,
    act_end: int,
) -> dict[str, Any]:
    """Load trajectory from a JSONL file."""
    import numpy as np

    states = []
    actions = []
    episodes = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            states.append(row["state"])
            actions.append(row["action"])
            episodes.append(row.get("episode", 0))

    state_arr = np.array(states, dtype=np.float64)
    action_arr = np.array(actions, dtype=np.float64)
    episode_arr = np.array(episodes, dtype=np.int64)

    ee_positions = state_arr[:, ee_start:ee_end]
    action_deltas = action_arr[:, act_start:act_end]

    next_ee = np.full_like(ee_positions, np.nan)
    for ep in np.unique(episode_arr):
        mask = episode_arr == ep
        indices = np.where(mask)[0]
        if len(indices) > 1:
            next_ee[indices[:-1]] = ee_positions[indices[1:]]

    return {
        "ee_positions": ee_positions,
        "actions": action_deltas,
        "episodes": episode_arr,
        "frames": np.arange(len(states)),
        "next_ee_positions": next_ee,
    }


# ── axiom gate ──────────────────────────────────────────────────────────


@app.command()
def gate(
    target: str = typer.Argument(
        ...,
        help="Target XYZ as 'x,y,z' (e.g. '0.8,0.3,0.2').",
    ),
    robot: str = typer.Option("ur5e", "--robot", "-r", help="Robot profile name."),
    mass: float = typer.Option(0.5, "--mass", "-m", help="Object mass in kg."),
    base_x: float = typer.Option(0.0, "--base-x", help="Robot base X position."),
    base_y: float = typer.Option(0.0, "--base-y", help="Robot base Y position."),
    base_z: float = typer.Option(0.0, "--base-z", help="Robot base Z position."),
    auto_fix: bool = typer.Option(
        False, "--auto-fix", help="Retry with suggested fix if within tolerance."
    ),
    max_deviation: float = typer.Option(
        0.05,
        "--max-deviation",
        help="Max acceptable patch deviation in metres (default 50mm).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Gate a proposed action: check feasibility and suggest fixes.

    Validates a target against physical constraints.  When the target is
    infeasible, computes the nearest feasible alternative and reports the
    deviation.  With ``--auto-fix``, accepts the patch only if it is
    within ``--max-deviation`` (default 50 mm).

    Verdicts::

        CAN             — target is feasible as proposed
        CAN_WITH_PATCH  — feasible only after adjustment (patch within tolerance)
        HARD_CANT       — infeasible, no acceptable patch available

    Examples::

        axiom gate 0.8,0.3,0.2 --robot ur3e --base-z 0.91
        axiom gate 0.8,0.3,0.2 --robot ur3e --base-z 0.91 --auto-fix
        axiom gate 0.8,0.3,0.2 --robot ur3e --auto-fix --max-deviation 0.01
    """
    import math

    from axiom_tfg.sdk import check_simple

    # Parse target
    try:
        target_xyz = [float(v) for v in target.split(",")]
        if len(target_xyz) != 3:
            raise ValueError
    except ValueError:
        typer.echo("ERROR: target must be 'x,y,z' (e.g. '0.8,0.3,0.2')", err=True)
        raise typer.Exit(code=1)

    base_xyz = [base_x, base_y, base_z]

    def _check(xyz: list[float]) -> dict[str, Any]:
        result = check_simple(
            target_xyz=xyz,
            robot=robot,
            mass_kg=mass,
            base_xyz=base_xyz,
        )
        patched_xyz = (
            result.top_fix_patch.get("projected_target_xyz")
            if result.top_fix_patch
            else None
        )
        return {
            "target_xyz": xyz,
            "verdict": result.verdict,
            "failed_gate": result.failed_gate,
            "reason": result.reason_code,
            "fix_type": result.top_fix,
            "fix_instruction": result.top_fix_instruction,
            "patched_xyz": patched_xyz,
            "validation_level": result.validation_level_reached,
        }

    # ── Attempt 1: original target ─────────────────────────────────────
    r = _check(target_xyz)

    # Compute deviation if a patch exists
    patch_delta_m: float | None = None
    if r["patched_xyz"]:
        patch_delta_m = math.sqrt(
            sum((a - b) ** 2 for a, b in zip(target_xyz, r["patched_xyz"]))
        )

    # ── Determine final verdict ────────────────────────────────────────
    if r["verdict"] == "CAN":
        final_verdict = "CAN"
        accepted_xyz = target_xyz
    elif auto_fix and r["patched_xyz"] and patch_delta_m is not None:
        # Check if the patch itself is feasible
        r2 = _check(r["patched_xyz"])
        if r2["verdict"] == "CAN" and patch_delta_m <= max_deviation:
            final_verdict = "CAN_WITH_PATCH"
            accepted_xyz = r["patched_xyz"]
        elif r2["verdict"] == "CAN":
            # Patch works but exceeds tolerance
            final_verdict = "HARD_CANT"
            accepted_xyz = None
        else:
            final_verdict = "HARD_CANT"
            accepted_xyz = None
    else:
        final_verdict = "HARD_CANT"
        accepted_xyz = None

    intent_risk = "NONE"
    if patch_delta_m is not None and r["verdict"] != "CAN":
        if patch_delta_m <= max_deviation:
            intent_risk = "LOW"
        else:
            intent_risk = "HIGH"

    # ── Build output ───────────────────────────────────────────────────
    output: dict[str, Any] = {
        "robot": robot,
        "base_xyz": base_xyz,
        "mass_kg": mass,
        "requested_target_xyz": target_xyz,
        "verdict": final_verdict,
        "failed_gate": r["failed_gate"],
        "reason": r["reason"],
        "fix_type": r["fix_type"],
        "fix_instruction": r["fix_instruction"],
        "patched_target_xyz": r["patched_xyz"],
        "patch_delta_m": round(patch_delta_m, 6) if patch_delta_m is not None else None,
        "patch_delta_mm": round(patch_delta_m * 1000, 1) if patch_delta_m is not None else None,
        "max_deviation_m": max_deviation,
        "intent_risk": intent_risk,
        "accepted_target_xyz": accepted_xyz,
        "patched_fields": ["target_xyz"] if final_verdict == "CAN_WITH_PATCH" else [],
        "validation_level": r["validation_level"],
    }

    if json_out:
        typer.echo(json.dumps(output, indent=2))
    else:
        typer.echo(f"\n  Target:  {_fmt_xyz(target_xyz)}")
        typer.echo(f"  Robot:   {robot}  base={_fmt_xyz(base_xyz)}  mass={mass}kg")
        typer.echo(f"  Verdict: {final_verdict}")

        if final_verdict == "CAN":
            typer.echo("  Target is feasible as proposed.")
        elif final_verdict == "CAN_WITH_PATCH":
            typer.echo(f"  Patched: {_fmt_xyz(r['patched_xyz'])}")
            typer.echo(f"  Delta:   {patch_delta_m * 1000:.1f}mm (within {max_deviation * 1000:.0f}mm tolerance)")
            typer.echo(f"  Risk:    {intent_risk}")
        else:
            typer.echo(f"  Gate:    {r['failed_gate']}")
            typer.echo(f"  Reason:  {r['reason']}")
            if r["fix_instruction"]:
                typer.echo(f"  Fix:     {r['fix_instruction']}")
            if patch_delta_m is not None:
                typer.echo(f"  Delta:   {patch_delta_m * 1000:.1f}mm (exceeds {max_deviation * 1000:.0f}mm tolerance)")
                typer.echo(f"  Risk:    {intent_risk}")

        typer.echo("")

    code = 0 if final_verdict in ("CAN", "CAN_WITH_PATCH") else 2
    raise typer.Exit(code=code)


# ── axiom atlas ─────────────────────────────────────────────────────────


@app.command()
def atlas(
    robot: str = typer.Argument(..., help="Robot profile name (e.g. 'ur3e', 'franka')."),
    compare: str = typer.Option(
        None, "--compare", "-c",
        help="Second robot for overlap comparison.",
    ),
    dataset: str = typer.Option(
        None, "--dataset", "-d",
        help="HuggingFace dataset ID for coverage overlay.",
    ),
    base_x: float = typer.Option(0.0, "--base-x", help="Robot base X position."),
    base_y: float = typer.Option(0.0, "--base-y", help="Robot base Y position."),
    base_z: float = typer.Option(0.0, "--base-z", help="Robot base Z position."),
    resolution: float = typer.Option(
        0.05, "--resolution", "-r",
        help="Grid resolution in metres (default 50mm).",
    ),
    max_episodes: int = typer.Option(
        None, "--max-episodes", "-n",
        help="Limit episodes when loading dataset.",
    ),
    ee_slice: str = typer.Option("0:3", "--ee-slice", help="Slice for EE position."),
    out: Path = typer.Option(
        None, "--out", "-o", help="Output directory for atlas artifacts.",
    ),
) -> None:
    """Map the feasible transformation space for a robot.

    Samples a 3D grid of end-effector positions and checks IK feasibility
    at each point, producing a capability map.

    Modes::

        axiom atlas ur3e --base-z 0.91
        axiom atlas ur3e --compare franka --base-z 0.91
        axiom atlas ur3e --dataset lerobot/libero_10 --base-z 0.91 -n 5
    """
    from axiom_tfg.atlas import (
        compute_coverage,
        compute_overlap,
        sample_feasible_space,
        write_atlas,
        write_coverage,
        write_overlap,
    )

    base_xyz = [base_x, base_y, base_z]

    if out is None:
        out = Path("artifacts") / "atlas"

    # ── Sample primary robot ───────────────────────────────────────
    typer.echo(f"Sampling feasible space for {robot} at base={base_xyz}...")
    typer.echo(f"  Resolution: {resolution*1000:.0f}mm")
    atlas_result = sample_feasible_space(
        robot=robot,
        base_xyz=base_xyz,
        resolution_m=resolution,
    )

    summary_path = write_atlas(atlas_result, out)
    typer.echo(f"\n  Atlas: {robot}")
    typer.echo(f"  Total points:  {atlas_result.total_points}")
    typer.echo(f"  Feasible:      {atlas_result.feasible_count} ({atlas_result.feasible_pct:.1f}%)")
    typer.echo(f"  Infeasible:    {atlas_result.infeasible_count}")

    # ── Overlap comparison ─────────────────────────────────────────
    if compare:
        typer.echo(f"\n  Computing overlap: {robot} vs {compare}...")
        overlap = compute_overlap(
            robot_a=robot,
            robot_b=compare,
            base_xyz=base_xyz,
            resolution_m=resolution,
        )
        write_overlap(overlap, out)
        typer.echo(f"\n  Overlap: {robot} vs {compare}")
        typer.echo(f"  Both feasible: {overlap.both_feasible}")
        typer.echo(f"  Only {robot}:  {overlap.only_a}")
        typer.echo(f"  Only {compare}:  {overlap.only_b}")
        typer.echo(f"  Overlap:       {overlap.overlap_pct:.1f}%")
        typer.echo(f"  {compare} covers {overlap.b_coverage_of_a:.1f}% of {robot}'s space")
        typer.echo(f"  {robot} covers {overlap.a_coverage_of_b:.1f}% of {compare}'s space")

    # ── Dataset coverage overlay ───────────────────────────────────
    if dataset:
        from axiom_tfg.audit import load_lerobot_trajectory

        ee_start, ee_end = (int(x) for x in ee_slice.split(":"))
        typer.echo(f"\n  Loading dataset: {dataset}...")
        data = load_lerobot_trajectory(
            dataset,
            max_episodes=max_episodes,
            ee_slice=(ee_start, ee_end),
        )
        ee_positions = data["ee_positions"]
        typer.echo(f"  Loaded {len(ee_positions)} EE positions")

        coverage = compute_coverage(atlas_result, ee_positions)
        write_coverage(coverage, out)
        typer.echo(f"\n  Coverage: {dataset} on {robot}")
        typer.echo(f"  Feasible voxels:     {coverage.total_feasible_voxels}")
        typer.echo(f"  Visited (feasible):  {coverage.occupied_feasible_voxels} ({coverage.space_coverage_pct:.1f}%)")
        typer.echo(f"  Data in feasible:    {coverage.data_in_feasible} / {coverage.total_data_points} ({coverage.data_feasibility_pct:.1f}%)")
        typer.echo(f"  Data in infeasible:  {coverage.data_in_infeasible}")

    typer.echo(f"\n  Artifacts: {out}/")


def _fmt_xyz(xyz: list[float]) -> str:
    """Format an XYZ list for display."""
    return f"[{xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f}]"


if __name__ == "__main__":
    app()
