"""Tests for the AI endpoints (mocked — no network calls)."""

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
app_module.SWEEPS_DIR = Path(_tmpdir) / "sweeps"
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
    assert data["provider"] == "gemini"
    assert data["model_used"] == "gemini-2.0-flash"
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
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env.pop("AXIOM_AI_DEMO_FALLBACK", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/ai/generate", json={"prompt": "hello"})
        assert resp.status_code == 503
        assert resp.headers["content-type"].startswith("application/json")
        assert "GOOGLE_API_KEY" in resp.json()["detail"]


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ai_generate_rejects_empty_prompt() -> None:
    resp = client.post("/ai/generate", json={"prompt": ""})
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/json")


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
    assert data["provider"] == "gemini"
    model.generate_content.assert_called_once()


def test_ai_explain_returns_503_without_key() -> None:
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env.pop("AXIOM_AI_DEMO_FALLBACK", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/ai/explain", json={"evidence": _SAMPLE_EVIDENCE})
        assert resp.status_code == 503
        assert resp.headers["content-type"].startswith("application/json")
        assert "GOOGLE_API_KEY" in resp.json()["detail"]


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ai_explain_rejects_empty_evidence() -> None:
    resp = client.post("/ai/explain", json={"evidence": None})
    assert resp.status_code == 400


# ── GET /ai/models ────────────────────────────────────────────────────────


def test_ai_models_returns_default_and_list() -> None:
    resp = client.get("/ai/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "default" in data
    assert "provider" in data
    assert isinstance(data["models"], list)
    assert len(data["models"]) >= 1
    assert data["default"] in data["models"]


# ── GET /ai/status ────────────────────────────────────────────────────────


def test_ai_status_has_full_fields() -> None:
    resp = client.get("/ai/status")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert "ai_enabled" in data
    assert "provider" in data
    assert "default_model" in data
    assert "demo_fallback_enabled" in data


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ai_status_enabled_with_key() -> None:
    resp = client.get("/ai/status")
    data = resp.json()
    assert data["ai_enabled"] is True
    assert data["provider"] == "gemini"


def test_ai_status_disabled_without_key() -> None:
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env.pop("AXIOM_AI_DEMO_FALLBACK", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.get("/ai/status")
        data = resp.json()
        assert data["ai_enabled"] is False
        assert "reason" in data


# ── 429 error handling ────────────────────────────────────────────────────


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
@patch("axiom_server.ai._get_model")
def test_ai_generate_returns_429_on_quota_error(mock_get_model: MagicMock) -> None:
    """When Gemini raises ResourceExhausted and fallback is off, return 429 JSON."""
    # Create an exception class that looks like ResourceExhausted.
    exc = type("ResourceExhausted", (Exception,), {})("quota exceeded")
    model = MagicMock()
    model.generate_content.side_effect = exc
    mock_get_model.return_value = model

    resp = client.post("/ai/generate", json={"prompt": "pick a box"})
    assert resp.status_code == 429
    assert resp.headers["content-type"].startswith("application/json")
    assert "quota" in resp.json()["detail"].lower()


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
@patch("axiom_server.ai._get_model")
def test_ai_generate_returns_502_on_unknown_error(mock_get_model: MagicMock) -> None:
    model = MagicMock()
    model.generate_content.side_effect = RuntimeError("some upstream issue")
    mock_get_model.return_value = model

    resp = client.post("/ai/generate", json={"prompt": "pick a box"})
    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"] == "AI upstream error"


# ── Fallback provider ────────────────────────────────────────────────────


@patch.dict(os.environ, {"AXIOM_AI_DEMO_FALLBACK": "true"})
def test_ai_generate_fallback_without_key() -> None:
    """With fallback enabled and no key, generate returns valid YAML via fallback."""
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env["AXIOM_AI_DEMO_FALLBACK"] = "true"
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/ai/generate", json={"prompt": "pick a 3kg box with franka"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "fallback"
    assert data["model_used"] == "fallback"
    assert "task_id" in data["yaml"]
    assert "3.0" in data["yaml"] or "3" in data["yaml"]
    assert "franka" in data["yaml"]


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key", "AXIOM_AI_DEMO_FALLBACK": "true"})
@patch("axiom_server.ai._get_model")
def test_ai_generate_falls_back_on_quota_error(mock_get_model: MagicMock) -> None:
    """With fallback enabled, quota error triggers fallback instead of 429."""
    exc = type("ResourceExhausted", (Exception,), {})("quota exceeded")
    model = MagicMock()
    model.generate_content.side_effect = exc
    mock_get_model.return_value = model

    resp = client.post("/ai/generate", json={"prompt": "pick a box"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "fallback"
    assert "task_id" in data["yaml"]


@patch.dict(os.environ, {"AXIOM_AI_DEMO_FALLBACK": "true"})
def test_ai_explain_fallback() -> None:
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env["AXIOM_AI_DEMO_FALLBACK"] = "true"
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/ai/explain", json={"evidence": _SAMPLE_EVIDENCE})
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "fallback"
    assert "reachability" in data["explanation"]


# ── OpenAI-compatible provider (Groq) ──────────────────────────────────


@patch.dict(os.environ, {"AXIOM_AI_PROVIDER": "openai", "AXIOM_OPENAI_API_KEY": "fake-groq-key"})
@patch("axiom_server.ai._get_openai_client")
def test_openai_generate_returns_yaml(mock_client_fn: MagicMock) -> None:
    """OpenAI provider generates YAML via chat completions."""
    mock_choice = MagicMock()
    mock_choice.message.content = _FAKE_YAML
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_client_fn.return_value = mock_client

    resp = client.post("/ai/generate", json={"prompt": "pick a 2kg box"})
    assert resp.status_code == 200
    data = resp.json()
    assert "yaml" in data
    assert "task_id" in data["yaml"]
    assert data["provider"] == "openai"
    assert data["model_used"] == "llama-3.3-70b-versatile"
    mock_client.chat.completions.create.assert_called_once()


@patch.dict(os.environ, {"AXIOM_AI_PROVIDER": "openai", "AXIOM_OPENAI_API_KEY": "fake-groq-key"})
@patch("axiom_server.ai._get_openai_client")
def test_openai_explain_returns_explanation(mock_client_fn: MagicMock) -> None:
    """OpenAI provider explains evidence via chat completions."""
    mock_choice = MagicMock()
    mock_choice.message.content = _FAKE_EXPLANATION
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_client_fn.return_value = mock_client

    resp = client.post("/ai/explain", json={"evidence": _SAMPLE_EVIDENCE})
    assert resp.status_code == 200
    data = resp.json()
    assert "explanation" in data
    assert len(data["explanation"]) > 0
    assert data["provider"] == "openai"


@patch.dict(os.environ, {"AXIOM_AI_PROVIDER": "openai", "AXIOM_OPENAI_API_KEY": "fake-groq-key"})
@patch("axiom_server.ai._get_openai_client")
def test_openai_generate_returns_429_on_rate_limit(mock_client_fn: MagicMock) -> None:
    """When Groq raises RateLimitError and fallback is off, return 429."""
    exc = type("RateLimitError", (Exception,), {})("rate limited")
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = exc
    mock_client_fn.return_value = mock_client

    resp = client.post("/ai/generate", json={"prompt": "pick a box"})
    assert resp.status_code == 429
    assert "quota" in resp.json()["detail"].lower()


def test_openai_generate_returns_503_without_key() -> None:
    """When provider is openai but no key is set, return 503."""
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env.pop("AXIOM_OPENAI_API_KEY", None)
    env.pop("AXIOM_AI_DEMO_FALLBACK", None)
    env["AXIOM_AI_PROVIDER"] = "openai"
    with patch.dict(os.environ, env, clear=True):
        resp = client.post("/ai/generate", json={"prompt": "hello"})
        assert resp.status_code == 503
        assert "AXIOM_OPENAI_API_KEY" in resp.json()["detail"]


@patch.dict(os.environ, {"AXIOM_AI_PROVIDER": "openai", "AXIOM_OPENAI_API_KEY": "fake-groq-key"})
def test_openai_models_returns_groq_defaults() -> None:
    """GET /ai/models with openai provider returns Groq model list."""
    resp = client.get("/ai/models")
    data = resp.json()
    assert data["provider"] == "openai"
    assert "llama-3.3-70b-versatile" in data["models"]
    assert data["default"] == "llama-3.3-70b-versatile"


@patch.dict(os.environ, {"AXIOM_AI_PROVIDER": "openai", "AXIOM_OPENAI_API_KEY": "fake-groq-key"})
def test_openai_status_shows_base_url() -> None:
    """GET /ai/status with openai provider includes base_url."""
    resp = client.get("/ai/status")
    data = resp.json()
    assert data["ai_enabled"] is True
    assert data["provider"] == "openai"
    assert "groq.com" in data["base_url"]


@patch.dict(os.environ, {"AXIOM_AI_PROVIDER": "openai", "AXIOM_OPENAI_API_KEY": "fake-groq-key"})
def test_openai_generate_rejects_bad_model() -> None:
    """Model not in openai allowlist returns 400."""
    resp = client.post("/ai/generate", json={"prompt": "hello", "model": "gpt-4"})
    assert resp.status_code == 400
    assert "allowlist" in resp.json()["detail"]


# ── Model validation ─────────────────────────────────────────────────────


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ai_generate_rejects_bad_model() -> None:
    resp = client.post("/ai/generate", json={"prompt": "hello", "model": "gpt-4"})
    assert resp.status_code == 400
    assert "allowlist" in resp.json()["detail"]


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
@patch("axiom_server.ai._get_model")
def test_ai_generate_accepts_allowlisted_model(mock_get_model: MagicMock) -> None:
    model = MagicMock()
    model.generate_content.return_value = _mock_response(_FAKE_YAML)
    mock_get_model.return_value = model

    resp = client.post("/ai/generate", json={"prompt": "pick a box", "model": "gemini-1.5-flash"})
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "gemini-1.5-flash"


# ── UI rendering ──────────────────────────────────────────────────────────


def test_ui_prompt_mode_elements_present() -> None:
    """GET / always contains Prompt Mode elements."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="promptInput"' in resp.text
    assert 'id="generateRunBtn"' in resp.text


def test_ui_hides_ai_when_key_missing() -> None:
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env.pop("AXIOM_AI_DEMO_FALLBACK", None)
    with patch.dict(os.environ, env, clear=True):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="promptInput"' in resp.text
        assert 'id="generateRunBtn"' in resp.text
        assert "disabled" in resp.text  # prompt + button disabled
        assert "AI disabled" in resp.text
        assert "AI_ENABLED = false" in resp.text


@patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
def test_ui_shows_ai_when_key_present() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'id="promptInput"' in resp.text
    assert 'id="generateRunBtn"' in resp.text
    assert "AI_ENABLED = true" in resp.text
    assert 'id="ai-model-select"' in resp.text


def test_ui_shows_ai_when_fallback_enabled() -> None:
    """Even without a key, if fallback is on the AI panel should render."""
    env = os.environ.copy()
    env.pop("GOOGLE_API_KEY", None)
    env["AXIOM_AI_DEMO_FALLBACK"] = "true"
    with patch.dict(os.environ, env, clear=True):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="generateRunBtn"' in resp.text
        assert 'id="ai-model-select"' in resp.text
        assert "AI_ENABLED = true" in resp.text
        assert "Demo fallback mode" in resp.text
