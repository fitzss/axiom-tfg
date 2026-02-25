"""``axiom init`` — scaffold a starter Axiom project in the current directory."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import yaml

from axiom_tfg.evidence import run_gates
from axiom_tfg.models import TaskSpec
from axiom_tfg.runner import run_taskspec, write_artifact_bundle


# ── Template data ─────────────────────────────────────────────────────────

_ROBOT_PROFILE = {
    "id": "ur5e",
    "base_pose": {"xyz": [0.0, 0.0, 0.0]},
    "max_reach_m": 1.85,
    "max_payload_kg": 5.0,
    "urdf_path": None,
    "base_link": None,
    "ee_link": None,
}

_ENV_PROFILE = {
    "safety_buffer": 0.02,
    "keepout_zones": [
        {
            "id": "conveyor_housing",
            "min_xyz": [0.3, 0.3, 0.0],
            "max_xyz": [0.7, 0.7, 1.0],
        }
    ],
}

_TASKS: dict[str, dict] = {
    "pick_place_can.yaml": {
        "task_id": "pick-place-001",
        "meta": {"template": "pick_and_place"},
        "substrate": {
            "id": "soda_can",
            "mass_kg": 0.35,
            "initial_pose": {"xyz": [1.0, 0.0, 0.8]},
        },
        "transformation": {
            "target_pose": {"xyz": [1.2, 0.3, 0.8]},
            "tolerance_m": 0.01,
        },
        "constructor": {
            "id": "ur5e",
            "base_pose": {"xyz": [0.0, 0.0, 0.0]},
            "max_reach_m": 1.85,
            "max_payload_kg": 5.0,
        },
        "allowed_adjustments": {
            "can_move_target": True,
            "can_move_base": False,
            "can_change_constructor": True,
            "can_split_payload": False,
        },
    },
    "pick_place_cant_payload.yaml": {
        "task_id": "pick-place-002-payload",
        "meta": {"template": "pick_and_place"},
        "substrate": {
            "id": "steel_beam",
            "mass_kg": 22.0,
            "initial_pose": {"xyz": [1.0, 0.0, 0.8]},
        },
        "transformation": {
            "target_pose": {"xyz": [1.2, 0.3, 0.8]},
            "tolerance_m": 0.01,
        },
        "constructor": {
            "id": "ur5e",
            "base_pose": {"xyz": [0.0, 0.0, 0.0]},
            "max_reach_m": 1.85,
            "max_payload_kg": 5.0,
        },
        "allowed_adjustments": {
            "can_move_target": False,
            "can_move_base": False,
            "can_change_constructor": True,
            "can_split_payload": True,
        },
    },
    "pick_place_cant_keepout.yaml": {
        "task_id": "pick-place-003-keepout",
        "meta": {"template": "pick_and_place"},
        "substrate": {
            "id": "widget",
            "mass_kg": 0.5,
            "initial_pose": {"xyz": [1.0, 0.0, 0.8]},
        },
        "transformation": {
            "target_pose": {"xyz": [0.5, 0.5, 0.5]},
            "tolerance_m": 0.01,
        },
        "constructor": {
            "id": "ur5e",
            "base_pose": {"xyz": [0.0, 0.0, 0.0]},
            "max_reach_m": 1.85,
            "max_payload_kg": 5.0,
        },
        "environment": {
            "safety_buffer": 0.02,
            "keepout_zones": [
                {
                    "id": "conveyor_housing",
                    "min_xyz": [0.3, 0.3, 0.0],
                    "max_xyz": [0.7, 0.7, 1.0],
                }
            ],
        },
        "allowed_adjustments": {
            "can_move_target": True,
            "can_move_base": False,
            "can_change_constructor": True,
            "can_split_payload": False,
        },
    },
}

_GITHUB_WORKFLOW = textwrap.dedent("""\
    name: Axiom Feasibility Gates
    on: [push, pull_request]

    jobs:
      axiom:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with:
              python-version: "3.12"
          - run: pip install axiom-tfg
          - name: Replay regression packs
            run: axiom replay regressions/ --out artifacts/replay
          - uses: actions/upload-artifact@v4
            if: always()
            with:
              name: axiom-artifacts
              path: artifacts/
""")

_MAKEFILE = textwrap.dedent("""\
    .PHONY: axiom-demo axiom-ci

    axiom-demo:
    \taxiom run tasks/pick_place_can.yaml --out artifacts/demo --junit
    \t@echo "--- Demo complete. See artifacts/demo/ ---"

    axiom-ci:
    \taxiom replay regressions/ --out artifacts/replay
    \t@echo "--- CI replay complete. See artifacts/replay/ ---"
""")


# ── Init logic ────────────────────────────────────────────────────────────


def _write_if_new(path: Path, content: str, force: bool) -> bool:
    """Write *content* to *path*.  Returns True if written, False if skipped."""
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _yaml_dump(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def scaffold(root: Path, *, force: bool = False) -> list[str]:
    """Create the starter project structure under *root*.

    Returns a list of human-readable status lines.
    """
    created: list[str] = []

    def _track(path: Path, content: str) -> None:
        rel = path.relative_to(root)
        if _write_if_new(path, content, force):
            created.append(f"  created {rel}")
        else:
            created.append(f"  exists  {rel} (skipped)")

    # 1. axiom_profiles/
    _track(
        root / "axiom_profiles" / "robot_ur5e.yaml",
        _yaml_dump(_ROBOT_PROFILE),
    )
    _track(
        root / "axiom_profiles" / "environment_default.yaml",
        _yaml_dump(_ENV_PROFILE),
    )

    # 2. tasks/ — three example TaskSpecs
    for name, data in _TASKS.items():
        _track(root / "tasks" / name, _yaml_dump(data))

    # 3. regressions/ — pre-built artifact bundles from those tasks
    for name, data in _TASKS.items():
        spec = TaskSpec.model_validate(data)
        result, packet = run_taskspec(spec)
        bundle_name = Path(name).stem
        bundle_dir = root / "regressions" / bundle_name
        bundle_dir.mkdir(parents=True, exist_ok=True)

        input_path = bundle_dir / "input.yaml"
        result_path = bundle_dir / "result.json"
        evidence_path = bundle_dir / "evidence.json"

        if input_path.exists() and not force:
            rel = bundle_dir.relative_to(root)
            created.append(f"  exists  {rel}/ (skipped)")
        else:
            write_artifact_bundle(spec, packet, result, bundle_dir)
            rel = bundle_dir.relative_to(root)
            created.append(f"  created {rel}/")

    # 4. .github/workflows/axiom.yml
    _track(root / ".github" / "workflows" / "axiom.yml", _GITHUB_WORKFLOW)

    # 5. Makefile
    _track(root / "Makefile", _MAKEFILE)

    return created
