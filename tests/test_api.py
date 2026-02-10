"""Tests for the FastAPI server."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Point data dir to a temp location before importing the app.
_tmpdir = tempfile.mkdtemp()
os.environ.setdefault("AXIOM_DATA_DIR", _tmpdir)

import axiom_server.app as app_module  # noqa: E402

app_module.DATA_DIR = Path(_tmpdir)
app_module.RUNS_DIR = Path(_tmpdir) / "runs"
app_module.store = app_module.RunStore(Path(_tmpdir) / "axiom.db")

from axiom_server.app import app  # noqa: E402

client = TestClient(app)

SAMPLE_YAML = """\
task_id: api-test-001
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
"""

FAILING_YAML = """\
task_id: api-test-fail
meta:
  template: pick_and_place

substrate:
  id: soda_can
  mass_kg: 0.35
  initial_pose:
    xyz: [1.0, 0.0, 0.8]

transformation:
  target_pose:
    xyz: [0.5, 0.5, 0.5]
  tolerance_m: 0.01

constructor:
  id: ur5e
  base_pose:
    xyz: [0.0, 0.0, 0.0]
  max_reach_m: 1.85
  max_payload_kg: 5.0

environment:
  safety_buffer: 0.02
  keepout_zones:
    - id: zone1
      min_xyz: [0.3, 0.3, 0.0]
      max_xyz: [0.7, 0.7, 1.0]

allowed_adjustments:
  can_move_target: true
"""


# ── health ────────────────────────────────────────────────────────────────


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── POST /runs ────────────────────────────────────────────────────────────


def test_post_run_yaml() -> None:
    resp = client.post("/runs", content=SAMPLE_YAML, headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "CAN"
    assert data["run_id"]
    assert data["evidence_url"].startswith("/runs/")

    # Evidence file must exist on disk.
    evidence_path = app_module.RUNS_DIR / data["run_id"] / "evidence.json"
    assert evidence_path.exists()
    evidence = json.loads(evidence_path.read_text())
    assert evidence["verdict"] == "CAN"


def test_post_run_json() -> None:
    import yaml as _yaml

    raw = _yaml.safe_load(SAMPLE_YAML)
    resp = client.post("/runs", json=raw)
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "CAN"


def test_post_run_failing() -> None:
    resp = client.post("/runs", content=FAILING_YAML, headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "HARD_CANT"
    assert data["failed_gate"] == "keepout"
    assert data["top_fix"] == "MOVE_TARGET"


def test_post_run_invalid_yaml() -> None:
    resp = client.post("/runs", content="meta: 123", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 422


# ── GET /runs ─────────────────────────────────────────────────────────────


def test_list_runs_returns_posted() -> None:
    # Post one run first.
    client.post("/runs", content=SAMPLE_YAML, headers={"Content-Type": "text/plain"})
    resp = client.get("/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) >= 1
    assert "run_id" in runs[0]


# ── GET /runs/{run_id} ───────────────────────────────────────────────────


def test_get_run_detail() -> None:
    post_resp = client.post("/runs", content=SAMPLE_YAML, headers={"Content-Type": "text/plain"})
    run_id = post_resp.json()["run_id"]
    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == run_id


def test_get_run_not_found() -> None:
    resp = client.get("/runs/nonexistent")
    assert resp.status_code == 404


# ── GET /runs/{run_id}/evidence ──────────────────────────────────────────


def test_get_evidence() -> None:
    post_resp = client.post("/runs", content=SAMPLE_YAML, headers={"Content-Type": "text/plain"})
    run_id = post_resp.json()["run_id"]
    resp = client.get(f"/runs/{run_id}/evidence")
    assert resp.status_code == 200
    evidence = resp.json()
    assert evidence["verdict"] == "CAN"
    assert "checks" in evidence


def test_get_evidence_not_found() -> None:
    resp = client.get("/runs/nonexistent/evidence")
    assert resp.status_code == 404


# ── GET / (web UI) ───────────────────────────────────────────────────────


def test_index_page() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "axiom-tfg" in resp.text
    assert "textarea" in resp.text


# ── GET /examples ────────────────────────────────────────────────────────


def test_list_examples() -> None:
    resp = client.get("/examples")
    assert resp.status_code == 200
    names = resp.json()
    assert isinstance(names, list)
    assert "pick_place_can.yaml" in names
    assert "pick_place_cant_reach.yaml" in names
    assert len(names) >= 4


def test_get_example_yaml() -> None:
    resp = client.get("/examples/pick_place_can.yaml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "task_id" in resp.text
    assert "pick_and_place" in resp.text


def test_get_example_not_found() -> None:
    resp = client.get("/examples/nonexistent.yaml")
    assert resp.status_code == 404


def test_get_example_path_traversal() -> None:
    resp = client.get("/examples/..%2Fpyproject.toml")
    assert resp.status_code in (400, 404)


# ── POST /runs returns top_fix_patch ─────────────────────────────────────


def test_post_run_returns_top_fix_patch() -> None:
    resp = client.post("/runs", content=FAILING_YAML, headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "HARD_CANT"
    assert data["top_fix_patch"] is not None
    assert data["top_fix_patch"]["kind"] == "MOVE_TARGET"
    assert len(data["top_fix_patch"]["new_xyz"]) == 3


def test_post_run_can_has_null_fix_patch() -> None:
    resp = client.post("/runs", content=SAMPLE_YAML, headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "CAN"
    assert data["top_fix_patch"] is None


# ── AXIOM_PUBLIC_BASE_URL ─────────────────────────────────────────────────


def test_evidence_url_relative_by_default() -> None:
    resp = client.post("/runs", content=SAMPLE_YAML, headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    assert resp.json()["evidence_url"].startswith("/runs/")


def test_evidence_url_absolute_with_base_url() -> None:
    with patch.object(app_module, "PUBLIC_BASE_URL", "https://axiom.example.com"):
        resp = client.post("/runs", content=SAMPLE_YAML, headers={"Content-Type": "text/plain"})
    assert resp.status_code == 200
    url = resp.json()["evidence_url"]
    assert url.startswith("https://axiom.example.com/runs/")


# ── GET /ai/status ────────────────────────────────────────────────────────


def test_ai_status_disabled_without_key() -> None:
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.get("/ai/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ai_enabled"] is False
    assert "reason" in data


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ai_status_enabled_with_key() -> None:
    resp = client.get("/ai/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ai_enabled"] is True
    assert "reason" not in data
