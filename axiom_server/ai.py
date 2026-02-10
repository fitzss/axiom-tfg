"""Gemini AI integration for TaskSpec generation and evidence explanation."""

from __future__ import annotations

import json
import os

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


def is_available() -> bool:
    """Return True if GOOGLE_API_KEY is set in the environment."""
    return bool(os.environ.get("GOOGLE_API_KEY"))


def _get_model() -> "google.generativeai.GenerativeModel":
    """Lazily import and configure the Gemini model."""
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    return genai.GenerativeModel("gemini-2.0-flash")


def generate_taskspec(prompt: str) -> str:
    """Use Gemini to generate a TaskSpec YAML from a natural-language prompt.

    Returns raw YAML text.
    """
    model = _get_model()
    response = model.generate_content(
        [
            {"role": "user", "parts": [_GENERATE_SYSTEM + "\n\nUser request: " + prompt]},
        ],
    )
    text = response.text.strip()
    # Strip markdown fences if the model wraps it.
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first and last fence lines.
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()
    return text


def explain_evidence(evidence: dict) -> str:
    """Use Gemini to produce a 1-sentence explanation of an EvidencePacket.

    Returns a plain-text string.
    """
    model = _get_model()
    evidence_json = json.dumps(evidence, indent=2)
    response = model.generate_content(
        [
            {"role": "user", "parts": [_EXPLAIN_SYSTEM + "\n\nEvidencePacket:\n" + evidence_json]},
        ],
    )
    return response.text.strip()
