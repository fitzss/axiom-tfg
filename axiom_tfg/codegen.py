"""LLM codegen adapter — natural language to validated robot actions.

Connects an LLM to the :func:`resolve` loop.  The LLM generates robot
actions from a plain-English task description; Axiom validates them
against physical constraints; if any action fails, the fix is fed back
to the LLM as a prompt constraint and it regenerates — automatically,
until the plan is physically valid or retries are exhausted.

Usage::

    from axiom_tfg.codegen import prompt_and_resolve

    result = prompt_and_resolve(
        "pick up the red box and place it in the bin",
        api_key="sk-...",
    )
    if result.resolved:
        for action in result.actions:
            print(f"Execute: move to {action['target_xyz']}")

For more control, build the callable yourself::

    from axiom_tfg.codegen import make_codegen_vla
    from axiom_tfg import resolve

    vla = make_codegen_vla(api_key="sk-...", robot="ur5e")
    result = resolve(vla, "stack the blocks", max_retries=5)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from axiom_tfg.resolve import Constraint, ResolveResult, resolve


# ── System prompt ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a robot action planner. Given a task description, output a JSON \
array of robot actions.

Robot: {robot}
  - Max reach from base: {max_reach_m} m
  - Max payload: {max_payload_kg} kg
  - Base position: origin [0, 0, 0]
  - Workspace: sphere of radius {max_reach_m} m centered at the base

Each action is a JSON object with:
  "target_xyz": [x, y, z]  — end-effector target in metres (REQUIRED)
  "mass_kg": <number>       — object mass in kg (optional, default 0.5)

Output ONLY a JSON array.  No explanation, no markdown, no code.

Example — pick a 0.3 kg part from the left, place it on the right:
[
  {{"target_xyz": [0.3, -0.3, 0.15], "mass_kg": 0.3}},
  {{"target_xyz": [0.3, 0.3, 0.20], "mass_kg": 0.3}}
]

Rules:
- All coordinates in metres.
- Keep targets within {max_reach_m} m of origin.
- Use realistic heights (table ≈ 0.1–0.2 m, shelf ≈ 0.4–0.8 m).
- For pick-then-place, output two actions (pick target, then place target).
"""

_CONSTRAINT_ADDENDUM = """
IMPORTANT — your previous plan was REJECTED.  You MUST fix it.

Physical constraint violations:
{constraints}

Generate a corrected JSON array that satisfies ALL of the above constraints.
"""


# ── Prompt construction ──────────────────────────────────────────────────


def _build_messages(
    task: str,
    constraints: list[Constraint],
    robot: str,
    max_reach_m: float,
    max_payload_kg: float,
) -> list[dict[str, str]]:
    """Build chat messages (system + user) for the LLM."""
    system = _SYSTEM_PROMPT.format(
        robot=robot,
        max_reach_m=max_reach_m,
        max_payload_kg=max_payload_kg,
    )

    user = task
    if constraints:
        lines: list[str] = []
        for i, c in enumerate(constraints, 1):
            line = f"  {i}. [{c.reason}] {c.instruction}"
            if c.proposed_patch:
                line += f"  →  suggested: {json.dumps(c.proposed_patch)}"
            lines.append(line)
        user += _CONSTRAINT_ADDENDUM.format(constraints="\n".join(lines))

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── Response parsing ─────────────────────────────────────────────────────


def _parse_actions(text: str) -> list[dict[str, Any]]:
    """Extract a list of action dicts from LLM output.

    Handles raw JSON, markdown-fenced JSON, and minor formatting quirks.
    """
    cleaned = text.strip()
    # Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    parsed = json.loads(cleaned)

    # Normalise single dict → list
    if isinstance(parsed, dict):
        parsed = [parsed]

    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array from LLM, got {type(parsed).__name__}")

    for i, action in enumerate(parsed):
        if "target_xyz" not in action:
            raise ValueError(f"Action {i} missing required field 'target_xyz'")
        xyz = action["target_xyz"]
        if not isinstance(xyz, list) or len(xyz) != 3:
            raise ValueError(f"Action {i} 'target_xyz' must be [x, y, z]")

    return parsed


# ── LLM call ─────────────────────────────────────────────────────────────


def _call_llm(
    messages: list[dict[str, str]],
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
) -> str:
    """Call an OpenAI-compatible chat API. Returns the assistant message."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


# ── Public API ───────────────────────────────────────────────────────────


def make_codegen_vla(
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    robot: str = "ur5e",
    max_reach_m: float = 0.85,
    max_payload_kg: float = 5.0,
    temperature: float = 0.2,
) -> Callable[[str, list[Constraint]], list[dict[str, Any]]]:
    """Create an LLM-backed callable for use with :func:`resolve`.

    Returns a function with signature
    ``(task: str, constraints: list[Constraint]) -> list[dict]``
    that calls an LLM to generate (or regenerate) robot actions.

    Supports any OpenAI-compatible API — OpenAI, Groq, Together,
    OpenRouter, Ollama, vLLM, etc.

    Args:
        model: Model name.  Defaults to env ``AXIOM_CODEGEN_MODEL``
            or ``"llama-3.3-70b-versatile"`` (Groq).
        api_key: API key.  Defaults to env ``AXIOM_OPENAI_API_KEY``.
        base_url: API base URL.  Defaults to env ``AXIOM_OPENAI_BASE_URL``
            or ``"https://api.groq.com/openai/v1"`` (Groq free tier).
        robot: Robot name included in the system prompt.
        max_reach_m: Max reach surfaced to the LLM (metres).
        max_payload_kg: Max payload surfaced to the LLM (kg).
        temperature: Sampling temperature (lower = more deterministic).
    """
    resolved_key = api_key or os.environ.get("AXIOM_OPENAI_API_KEY", "")
    resolved_url = base_url or os.environ.get(
        "AXIOM_OPENAI_BASE_URL", "https://api.groq.com/openai/v1"
    )
    resolved_model = model or os.environ.get(
        "AXIOM_CODEGEN_MODEL", "llama-3.3-70b-versatile"
    )

    if not resolved_key:
        raise ValueError(
            "No API key provided. Pass api_key= or set AXIOM_OPENAI_API_KEY."
        )

    def vla(task: str, constraints: list[Constraint]) -> list[dict[str, Any]]:
        messages = _build_messages(
            task, constraints, robot, max_reach_m, max_payload_kg
        )
        raw = _call_llm(
            messages,
            model=resolved_model,
            api_key=resolved_key,
            base_url=resolved_url,
            temperature=temperature,
        )
        return _parse_actions(raw)

    return vla


def prompt_and_resolve(
    task: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    robot: str = "ur5e",
    max_reach_m: float = 0.85,
    max_payload_kg: float = 5.0,
    max_retries: int = 3,
    temperature: float = 0.2,
    keepout_zones: list[dict[str, Any]] | None = None,
) -> ResolveResult:
    """Natural language → validated robot actions.  One function call.

    Connects an LLM to the Axiom :func:`resolve` loop::

        English prompt
          → LLM generates actions with target poses
          → Axiom validates physics (IK, reach, payload, keepout)
          → if blocked: fix fed back to LLM as constraint
          → LLM regenerates
          → repeat until valid (or max_retries exhausted)

    Args:
        task: Plain-English task description, e.g.
            ``"pick up the mug and put it on the shelf"``
        model: LLM model name.
        api_key: API key (or set ``AXIOM_OPENAI_API_KEY``).
        base_url: API base URL (or set ``AXIOM_OPENAI_BASE_URL``).
        robot: Robot model (default ``"ur5e"``).
        max_reach_m: Robot max reach in metres.
        max_payload_kg: Robot max payload in kg.
        max_retries: Max LLM re-generation attempts after first failure.
        temperature: LLM sampling temperature.
        keepout_zones: Forbidden regions (list of
            ``{"id": ..., "min_xyz": [...], "max_xyz": [...]}``).

    Returns:
        :class:`~axiom_tfg.resolve.ResolveResult` with ``resolved=True``
        if the LLM produced a physically valid plan within the retry budget.

    Example::

        from axiom_tfg.codegen import prompt_and_resolve

        result = prompt_and_resolve(
            "pick up the red box and place it in the bin",
            api_key="sk-...",
        )
        if result.resolved:
            for action in result.actions:
                print(f"Move to {action['target_xyz']}")
        else:
            print(f"Could not resolve: {result.constraints[-1].instruction}")
    """
    vla = make_codegen_vla(
        model=model,
        api_key=api_key,
        base_url=base_url,
        robot=robot,
        max_reach_m=max_reach_m,
        max_payload_kg=max_payload_kg,
        temperature=temperature,
    )
    return resolve(
        vla,
        task,
        robot=robot,
        max_retries=max_retries,
        max_reach_m=max_reach_m,
        max_payload_kg=max_payload_kg,
        keepout_zones=keepout_zones,
    )
