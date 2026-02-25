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


if __name__ == "__main__":
    app()
