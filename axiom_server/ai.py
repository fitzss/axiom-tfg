"""AI integration: Gemini, OpenAI-compatible (Groq/Together/OpenRouter), and demo fallback."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from fastapi import HTTPException

# ── Configuration (env vars) ────────────────────────────────────────────

_DEFAULT_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

_DEFAULT_OPENAI_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]


def _cfg_provider() -> str:
    return os.environ.get("AXIOM_AI_PROVIDER", "gemini").lower()


def _cfg_default_model() -> str:
    provider = _cfg_provider()
    if provider == "openai":
        return os.environ.get("AXIOM_OPENAI_MODEL_DEFAULT", "llama-3.3-70b-versatile")
    return os.environ.get("AXIOM_GEMINI_MODEL_DEFAULT", "gemini-2.0-flash")


def _cfg_models_allowlist() -> list[str]:
    provider = _cfg_provider()
    if provider == "openai":
        raw = os.environ.get("AXIOM_OPENAI_MODELS_ALLOWLIST", "")
        if raw.strip():
            return [m.strip() for m in raw.split(",") if m.strip()]
        return list(_DEFAULT_OPENAI_MODELS)
    raw = os.environ.get("AXIOM_GEMINI_MODELS_ALLOWLIST", "")
    if raw.strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(_DEFAULT_GEMINI_MODELS)


def _cfg_demo_fallback() -> bool:
    return os.environ.get("AXIOM_AI_DEMO_FALLBACK", "false").lower() in ("true", "1", "yes")


def _cfg_openai_base_url() -> str:
    return os.environ.get("AXIOM_OPENAI_BASE_URL", "https://api.groq.com/openai/v1")


def _cfg_openai_api_key() -> str:
    return os.environ.get("AXIOM_OPENAI_API_KEY", "")


# ── Public config queries ───────────────────────────────────────────────


def get_provider() -> str:
    return _cfg_provider()


def get_default_model() -> str:
    return _cfg_default_model()


def get_models_allowlist() -> list[str]:
    return _cfg_models_allowlist()


def is_demo_fallback_enabled() -> bool:
    return _cfg_demo_fallback()


def _has_api_key() -> bool:
    """Return True if the configured provider has its API key set."""
    provider = _cfg_provider()
    if provider == "gemini":
        return bool(os.environ.get("GOOGLE_API_KEY"))
    if provider == "openai":
        return bool(_cfg_openai_api_key())
    return False


def is_available() -> bool:
    """Return True if AI generation can proceed (provider key set OR fallback enabled)."""
    if _cfg_provider() in ("gemini", "openai") and _has_api_key():
        return True
    return _cfg_demo_fallback()


def get_status() -> dict[str, Any]:
    """Return full AI status dict for /ai/status."""
    provider = _cfg_provider()
    has_key = _has_api_key()
    fallback = _cfg_demo_fallback()
    enabled = is_available()

    active_provider: str
    if provider in ("gemini", "openai") and has_key:
        active_provider = provider
    elif fallback:
        active_provider = "fallback"
    else:
        active_provider = "none"

    result: dict[str, Any] = {
        "ai_enabled": enabled,
        "provider": active_provider,
        "default_model": _cfg_default_model(),
        "demo_fallback_enabled": fallback,
    }
    if not enabled:
        if provider == "none":
            result["reason"] = "AXIOM_AI_PROVIDER is set to none"
        elif provider == "openai" and not has_key:
            result["reason"] = "AXIOM_OPENAI_API_KEY is not set"
        elif not has_key:
            result["reason"] = "GOOGLE_API_KEY is not set"
    if provider == "openai" and has_key:
        result["base_url"] = _cfg_openai_base_url()
    return result


# ── Prompt templates ────────────────────────────────────────────────────

TASKSPEC_SCHEMA_EXAMPLE = """\
task_id: my-task-001
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

environment:
  safety_buffer: 0.02
  keepout_zones:
    - id: conveyor_housing
      min_xyz: [0.3, 0.3, 0.0]
      max_xyz: [0.7, 0.7, 1.0]

allowed_adjustments:
  can_move_target: true
  can_move_base: false
  can_change_constructor: true
  can_split_payload: false
"""

_GENERATE_SYSTEM = """\
You are a robotics task-spec generator. Given a natural-language description,
produce a valid TaskSpec YAML document. Return ONLY the YAML — no markdown
fences, no commentary.

The schema requires these top-level keys:
- task_id (string)
- meta.template (string, usually "pick_and_place")
- substrate.id (string), substrate.mass_kg (positive float),
  substrate.initial_pose.xyz ([x,y,z])
- transformation.target_pose.xyz ([x,y,z]),
  transformation.tolerance_m (positive float)
- constructor.id (string), constructor.base_pose.xyz ([x,y,z]),
  constructor.max_reach_m (positive float),
  constructor.max_payload_kg (positive float)
- environment (optional): safety_buffer (float >= 0, default 0.02),
  keepout_zones (list of {id, min_xyz, max_xyz})
- allowed_adjustments (optional): can_move_target, can_move_base,
  can_change_constructor, can_split_payload (booleans, default false)

All xyz values are 3-element float lists. Use realistic values in metres and kg.

Here is an example of a valid TaskSpec YAML:
""" + TASKSPEC_SCHEMA_EXAMPLE

_EXPLAIN_SYSTEM = """\
You are a robotics feasibility analyst. Given an EvidencePacket JSON from a
deterministic gate linter, produce exactly ONE sentence that:
1) States the failure reason in plain English (or confirms feasibility).
2) If a counterfactual fix exists, suggests the minimal corrective action.
Keep it concise and actionable. Do not use markdown. One sentence only.
"""


# ── Exception mapping ───────────────────────────────────────────────────


def _map_upstream_exception(exc: Exception) -> HTTPException:
    """Map provider exceptions to HTTPException with JSON detail."""
    exc_type = type(exc).__name__

    # google.api_core.exceptions
    if "ResourceExhausted" in exc_type:
        return HTTPException(status_code=429, detail="AI quota exceeded")
    if "PermissionDenied" in exc_type:
        return HTTPException(status_code=403, detail="AI auth error")
    if "Unauthenticated" in exc_type:
        return HTTPException(status_code=401, detail="AI auth error")
    if "InvalidArgument" in exc_type:
        return HTTPException(status_code=400, detail="AI bad request")

    # openai SDK exceptions
    if "RateLimitError" in exc_type:
        return HTTPException(status_code=429, detail="AI quota exceeded")
    if "AuthenticationError" in exc_type:
        return HTTPException(status_code=401, detail="AI auth error")
    if "PermissionDeniedError" in exc_type:
        return HTTPException(status_code=403, detail="AI auth error")
    if "BadRequestError" in exc_type:
        return HTTPException(status_code=400, detail="AI bad request")

    return HTTPException(status_code=502, detail="AI upstream error")


# ── Gemini backend ──────────────────────────────────────────────────────


def _get_model(model_name: str | None = None) -> "google.generativeai.GenerativeModel":
    """Lazily import and configure the Gemini model."""
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    name = model_name or _cfg_default_model()
    return genai.GenerativeModel(name)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.startswith("```")]
        text = "\n".join(lines).strip()
    return text


def _gemini_generate(prompt: str, model_name: str | None = None) -> tuple[str, str]:
    """Call Gemini to generate YAML. Returns (yaml_text, model_used)."""
    name = model_name or _cfg_default_model()
    model = _get_model(name)
    response = model.generate_content(
        [{"role": "user", "parts": [_GENERATE_SYSTEM + "\n\nUser request: " + prompt]}],
    )
    return _strip_fences(response.text), name


def _gemini_explain(evidence: dict, model_name: str | None = None) -> tuple[str, str]:
    """Call Gemini to explain evidence. Returns (explanation, model_used)."""
    name = model_name or _cfg_default_model()
    model = _get_model(name)
    evidence_json = json.dumps(evidence, indent=2)
    response = model.generate_content(
        [{"role": "user", "parts": [_EXPLAIN_SYSTEM + "\n\nEvidencePacket:\n" + evidence_json]}],
    )
    return response.text.strip(), name


# ── OpenAI-compatible backend (Groq, Together, OpenRouter, etc.) ────────


def _get_openai_client() -> "openai.OpenAI":
    """Create an OpenAI client pointed at the configured base URL."""
    from openai import OpenAI

    return OpenAI(
        api_key=_cfg_openai_api_key(),
        base_url=_cfg_openai_base_url(),
    )


def _openai_generate(prompt: str, model_name: str | None = None) -> tuple[str, str]:
    """Call an OpenAI-compatible API to generate YAML. Returns (yaml_text, model_used)."""
    name = model_name or _cfg_default_model()
    client = _get_openai_client()
    response = client.chat.completions.create(
        model=name,
        messages=[
            {"role": "system", "content": _GENERATE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    text = response.choices[0].message.content or ""
    return _strip_fences(text), name


def _openai_explain(evidence: dict, model_name: str | None = None) -> tuple[str, str]:
    """Call an OpenAI-compatible API to explain evidence. Returns (explanation, model_used)."""
    name = model_name or _cfg_default_model()
    client = _get_openai_client()
    evidence_json = json.dumps(evidence, indent=2)
    response = client.chat.completions.create(
        model=name,
        messages=[
            {"role": "system", "content": _EXPLAIN_SYSTEM},
            {"role": "user", "content": evidence_json},
        ],
        temperature=0.2,
    )
    text = response.choices[0].message.content or ""
    return text.strip(), name


# ── Demo fallback (local, deterministic) ────────────────────────────────

_ROBOT_DB: dict[str, dict] = {
    "ur5e":   {"max_reach_m": 1.85, "max_payload_kg": 5.0},
    "ur10e":  {"max_reach_m": 1.30, "max_payload_kg": 12.5},
    "franka": {"max_reach_m": 0.855, "max_payload_kg": 3.0},
    "fanuc":  {"max_reach_m": 2.0, "max_payload_kg": 7.0},
}


def _fallback_generate(prompt: str) -> str:
    """Parse simple patterns from the prompt and emit valid TaskSpec YAML."""
    lower = prompt.lower()

    mass_match = re.search(r"(\d+\.?\d*)\s*kg", lower)
    mass = float(mass_match.group(1)) if mass_match else 0.35

    xyz_pattern = r"\[\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\]"
    xyz_matches = re.findall(xyz_pattern, prompt)
    initial_xyz = [float(x) for x in xyz_matches[0]] if len(xyz_matches) >= 1 else [1.0, 0.0, 0.8]
    target_xyz = [float(x) for x in xyz_matches[1]] if len(xyz_matches) >= 2 else [1.2, 0.3, 0.8]

    robot = "ur5e"
    for name in _ROBOT_DB:
        if name in lower:
            robot = name
            break

    specs = _ROBOT_DB.get(robot, _ROBOT_DB["ur5e"])

    sub_id = "object"
    for word in ["box", "can", "bottle", "part", "soda_can", "pallet", "widget", "cup"]:
        if word in lower:
            sub_id = word
            break

    return f"""\
task_id: generated-fallback
meta:
  template: pick_and_place

substrate:
  id: {sub_id}
  mass_kg: {mass}
  initial_pose:
    xyz: [{initial_xyz[0]}, {initial_xyz[1]}, {initial_xyz[2]}]

transformation:
  target_pose:
    xyz: [{target_xyz[0]}, {target_xyz[1]}, {target_xyz[2]}]
  tolerance_m: 0.01

constructor:
  id: {robot}
  base_pose:
    xyz: [0.0, 0.0, 0.0]
  max_reach_m: {specs['max_reach_m']}
  max_payload_kg: {specs['max_payload_kg']}

allowed_adjustments:
  can_move_target: true
  can_move_base: false
  can_change_constructor: true
  can_split_payload: false"""


def _fallback_explain(evidence: dict) -> str:
    """Deterministically summarize the EvidencePacket in one sentence."""
    verdict = evidence.get("verdict", "UNKNOWN")
    if verdict == "CAN":
        return "All feasibility gates passed; the task is feasible as specified."

    gate = evidence.get("failed_gate", "unknown")
    reason = "unknown"
    for check in evidence.get("checks", []):
        if check.get("reason_code"):
            reason = check["reason_code"]

    fixes = evidence.get("counterfactual_fixes", [])
    if fixes:
        fix = fixes[0]
        return (
            f"Task failed at the {gate} gate ({reason}); "
            f"suggested fix: {fix.get('instruction', fix.get('type', 'N/A'))}."
        )
    return f"Task failed at the {gate} gate ({reason}); no automatic fix available."


# ── Public API (used by app.py endpoints) ───────────────────────────────


def validate_model(model_name: str | None) -> str | None:
    """Validate model is in allowlist. Returns the resolved name or raises HTTPException."""
    if model_name is None:
        return None
    allowlist = _cfg_models_allowlist()
    if model_name not in allowlist:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_name}' not in allowlist: {allowlist}",
        )
    return model_name


def _dispatch_generate(prompt: str, model: str | None) -> tuple[str, str, str]:
    """Route to the correct backend. Returns (text, model_used, provider)."""
    provider = _cfg_provider()
    if provider == "openai":
        text, name = _openai_generate(prompt, model)
        return text, name, "openai"
    else:
        text, name = _gemini_generate(prompt, model)
        return text, name, "gemini"


def _dispatch_explain(evidence: dict, model: str | None) -> tuple[str, str, str]:
    """Route to the correct backend. Returns (text, model_used, provider)."""
    provider = _cfg_provider()
    if provider == "openai":
        text, name = _openai_explain(evidence, model)
        return text, name, "openai"
    else:
        text, name = _gemini_explain(evidence, model)
        return text, name, "gemini"


def generate_taskspec(prompt: str, model: str | None = None) -> dict[str, str]:
    """Generate a TaskSpec YAML. Returns {"yaml", "model_used", "provider"}.

    Falls back to local generator when upstream fails and fallback is enabled.
    """
    provider = _cfg_provider()
    has_key = _has_api_key()
    fallback = _cfg_demo_fallback()

    if provider == "none" or (not has_key and fallback):
        return {
            "yaml": _fallback_generate(prompt),
            "model_used": "fallback",
            "provider": "fallback",
        }

    if not has_key:
        key_name = "AXIOM_OPENAI_API_KEY" if provider == "openai" else "GOOGLE_API_KEY"
        raise HTTPException(
            status_code=503,
            detail=f"{key_name} is not set — AI features are unavailable.",
        )

    try:
        yaml_text, model_used, prov = _dispatch_generate(prompt, model)
        return {"yaml": yaml_text, "model_used": model_used, "provider": prov}
    except HTTPException:
        raise
    except Exception as exc:
        if fallback:
            return {
                "yaml": _fallback_generate(prompt),
                "model_used": "fallback",
                "provider": "fallback",
            }
        raise _map_upstream_exception(exc)


def explain_evidence(evidence: dict, model: str | None = None) -> dict[str, str]:
    """Explain an EvidencePacket. Returns {"explanation", "model_used", "provider"}.

    Falls back to local explainer when upstream fails and fallback is enabled.
    """
    provider = _cfg_provider()
    has_key = _has_api_key()
    fallback = _cfg_demo_fallback()

    if provider == "none" or (not has_key and fallback):
        return {
            "explanation": _fallback_explain(evidence),
            "model_used": "fallback",
            "provider": "fallback",
        }

    if not has_key:
        key_name = "AXIOM_OPENAI_API_KEY" if provider == "openai" else "GOOGLE_API_KEY"
        raise HTTPException(
            status_code=503,
            detail=f"{key_name} is not set — AI features are unavailable.",
        )

    try:
        explanation, model_used, prov = _dispatch_explain(evidence, model)
        return {"explanation": explanation, "model_used": model_used, "provider": prov}
    except HTTPException:
        raise
    except Exception as exc:
        if fallback:
            return {
                "explanation": _fallback_explain(evidence),
                "model_used": "fallback",
                "provider": "fallback",
            }
        raise _map_upstream_exception(exc)
