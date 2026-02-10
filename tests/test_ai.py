"""Tests for the Gemini AI endpoints (mocked — no network calls)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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

_FAKE_YAML = """\
task_id: generated-001
meta:
  template: pick_and_place
substrate:
  id: box
  mass_kg: 2.0
  initial_pose:
    xyz: [1.0, 0.0, 1.0]
transformation:
  target_pose:
    xyz: [2.0, 1.0, 0.5]
  tolerance_m: 0.01
constructor:
  id: ur5e
  base_pose:
    xyz: [0.0, 0.0, 0.0]
  max_reach_m: 1.85
  max_payload_kg: 5.0
"""

_FAKE_EXPLANATION = "The target is outside the robot's 1.85 m reach sphere; move the target 0.42 m closer to the base."

_SAMPLE_EVIDENCE = {
    "task_id": "test-001",
    "verdict": "HARD_CANT",
    "failed_gate": "reachability",
    "checks": [
        {
            "gate_name": "reachability",
            "status": "FAIL",
            "measured_values": {"distance_m": 2.27, "max_reach_m": 1.85},
            "reason_code": "OUT_OF_REACH",
        }
    ],
    "counterfactual_fixes": [
        {
            "type": "MOVE_TARGET",
            "delta": 0.42,
            "instruction": "Move target 0.4200 m closer to base (projected onto reach sphere).",
            "proposed_patch": {"projected_target_xyz": [1.63, 0.0, 0.0]},
        }
    ],
}


def _mock_response(text: str) -> MagicMock:
    """Create a mock Gemini response object."""
    resp = MagicMock()
    resp.text = text
    return resp


# ── POST /ai/generate ────────────────────────────────────────────────────


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
@patch("axiom_server.ai._get_model")
def test_ai_generate_returns_yaml(mock_get_model: MagicMock) -> None:
    model = MagicMock()
    model.generate_content.return_value = _mock_response(_FAKE_YAML)
    mock_get_model.return_value = model

    resp = client.post("/ai/generate", json={"prompt": "pick a 2kg box"})
    assert resp.status_code == 200
    data = resp.json()
    assert "yaml" in data
    assert "task_id" in data["yaml"]
    model.generate_content.assert_called_once()


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
@patch("axiom_server.ai._get_model")
def test_ai_generate_strips_markdown_fences(mock_get_model: MagicMock) -> None:
    fenced = "```yaml\n" + _FAKE_YAML + "\n```"
    model = MagicMock()
    model.generate_content.return_value = _mock_response(fenced)
    mock_get_model.return_value = model

    resp = client.post("/ai/generate", json={"prompt": "pick a box"})
    assert resp.status_code == 200
    assert "```" not in resp.json()["yaml"]


def test_ai_generate_returns_503_without_key() -> None:
    # Ensure GOOGLE_API_KEY is not set for this test.
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/ai/generate", json={"prompt": "hello"})
        assert resp.status_code == 503
        assert "GOOGLE_API_KEY" in resp.json()["detail"]


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ai_generate_rejects_empty_prompt() -> None:
    resp = client.post("/ai/generate", json={"prompt": ""})
    assert resp.status_code == 400


# ── POST /ai/explain ─────────────────────────────────────────────────────


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
@patch("axiom_server.ai._get_model")
def test_ai_explain_returns_explanation(mock_get_model: MagicMock) -> None:
    model = MagicMock()
    model.generate_content.return_value = _mock_response(_FAKE_EXPLANATION)
    mock_get_model.return_value = model

    resp = client.post("/ai/explain", json={"evidence": _SAMPLE_EVIDENCE})
    assert resp.status_code == 200
    data = resp.json()
    assert "explanation" in data
    assert len(data["explanation"]) > 0
    model.generate_content.assert_called_once()


def test_ai_explain_returns_503_without_key() -> None:
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/ai/explain", json={"evidence": _SAMPLE_EVIDENCE})
        assert resp.status_code == 503
        assert "GOOGLE_API_KEY" in resp.json()["detail"]


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ai_explain_rejects_empty_evidence() -> None:
    resp = client.post("/ai/explain", json={"evidence": None})
    assert resp.status_code == 400


# ── UI hides AI panel when key missing ───────────────────────────────────


def test_ui_hides_ai_when_key_missing() -> None:
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.get("/")
        assert resp.status_code == 200
        # The interactive AI panel (input + button) must not render.
        assert 'id="ai-gen-btn"' not in resp.text
        assert 'id="ai-prompt"' not in resp.text
        # The disabled banner should be shown instead.
        assert "AI disabled" in resp.text
        # AI_ENABLED should be false in JS
        assert "AI_ENABLED = false" in resp.text


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ui_shows_ai_when_key_present() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AI Assistant" in resp.text
    assert "AI_ENABLED = true" in resp.text
