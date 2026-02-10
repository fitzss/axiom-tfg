"""Typer CLI entry-point for axiom-tfg."""

from __future__ import annotations

from pathlib import Path

import typer

from axiom_tfg.evidence import (
    load_task_spec,
    run_gates,
    validate_task_spec,
    write_evidence,
)
from axiom_tfg.models import EvidencePacket, Verdict

app = typer.Typer(
    name="tfg",
    help="Deterministic physical-task feasibility gate linter.",
    add_completion=False,
)

# examples/ lives at repo root, one level above axiom_tfg/
_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

_DEMO_YAMLS = [
    "pick_place_can.yaml",
    "pick_place_cant_reach.yaml",
    "pick_place_cant_payload.yaml",
    "pick_place_cant_keepout.yaml",
]


@app.command()
def run(
    task_yaml: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to a TaskSpec YAML file.",
    ),
    out: Path = typer.Option(
        Path("out"),
        "--out",
        "-o",
        help="Output directory for evidence packets.",
    ),
) -> None:
    """Parse a TaskSpec YAML, run feasibility gates, and emit an EvidencePacket."""
    spec = load_task_spec(task_yaml)
    packet = run_gates(spec)
    evidence_path = write_evidence(packet, out)

    if packet.verdict == Verdict.CAN:
        typer.echo(f"CAN: all gates passed")
    else:
        # Use first fix instruction as the summary hint.
        first_check = next(
            (c for c in packet.checks if c.reason_code), None
        )
        reason = first_check.reason_code if first_check else "unknown"
        hint = (
            packet.counterfactual_fixes[0].instruction
            if packet.counterfactual_fixes
            else "no fix available"
        )
        typer.echo(f"HARD_CANT: {reason} — {hint}")

    typer.echo(f"Evidence: {evidence_path}")

    raise typer.Exit(code=0 if packet.verdict == Verdict.CAN else 1)


@app.command()
def validate(
    task_yaml: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to a TaskSpec YAML file.",
    ),
) -> None:
    """Validate a TaskSpec YAML against the schema (no gates executed)."""
    errors = validate_task_spec(task_yaml)
    if not errors:
        typer.echo("OK")
        raise typer.Exit(code=0)

    typer.echo("Validation errors:")
    for err in errors:
        typer.echo(f"  - {err}")
    raise typer.Exit(code=1)


def _format_row(filename: str, packet: EvidencePacket) -> str:
    """Build one row of the demo summary table."""
    verdict = packet.verdict.value
    gate = packet.failed_gate or "-"
    if packet.counterfactual_fixes:
        top = packet.counterfactual_fixes[0].type.value
    else:
        top = "-"
    return f"  {filename:<35s} {verdict:<12s} {gate:<15s} {top}"


@app.command()
def demo(
    out: Path = typer.Option(
        Path("out"),
        "--out",
        "-o",
        help="Output directory for evidence packets.",
    ),
) -> None:
    """Run the bundled example YAMLs and print a summary table."""
    if not _EXAMPLES_DIR.is_dir():
        typer.echo(f"Examples directory not found: {_EXAMPLES_DIR}")
        raise typer.Exit(code=1)

    typer.echo(f"  {'FILE':<35s} {'VERDICT':<12s} {'FAILED_GATE':<15s} TOP_FIX")
    typer.echo(f"  {'─' * 35} {'─' * 12} {'─' * 15} {'─' * 20}")

    for name in _DEMO_YAMLS:
        yaml_path = _EXAMPLES_DIR / name
        spec = load_task_spec(yaml_path)
        packet = run_gates(spec)
        write_evidence(packet, out)
        typer.echo(_format_row(name, packet))

    typer.echo(f"\nEvidence written to: {out}/")


if __name__ == "__main__":
    app()
