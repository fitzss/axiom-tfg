"""Tests for the LLM codegen adapter."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from axiom_tfg.codegen import (
    _build_messages,
    _parse_actions,
    make_codegen_vla,
    prompt_and_resolve,
)
from axiom_tfg.resolve import Constraint


# ── _parse_actions (pure, no mocking) ────────────────────────────────────


class TestParseActions:
    """Parse LLM text into action dicts."""

    def test_parse_json_array(self) -> None:
        text = '[{"target_xyz": [0.3, 0.2, 0.1]}]'
        actions = _parse_actions(text)
        assert len(actions) == 1
        assert actions[0]["target_xyz"] == [0.3, 0.2, 0.1]

    def test_parse_multi_action(self) -> None:
        text = json.dumps([
            {"target_xyz": [0.3, 0.2, 0.1], "mass_kg": 0.5},
            {"target_xyz": [0.4, -0.1, 0.3], "mass_kg": 0.5},
        ])
        actions = _parse_actions(text)
        assert len(actions) == 2

    def test_parse_markdown_fenced(self) -> None:
        text = '```json\n[{"target_xyz": [0.3, 0.2, 0.1]}]\n```'
        actions = _parse_actions(text)
        assert len(actions) == 1

    def test_parse_markdown_no_language(self) -> None:
        text = '```\n[{"target_xyz": [0.3, 0.2, 0.1]}]\n```'
        actions = _parse_actions(text)
        assert len(actions) == 1

    def test_parse_single_dict_normalized(self) -> None:
        text = '{"target_xyz": [0.3, 0.2, 0.1]}'
        actions = _parse_actions(text)
        assert len(actions) == 1

    def test_parse_with_whitespace(self) -> None:
        text = '  \n [{"target_xyz": [0.3, 0.2, 0.1]}] \n  '
        actions = _parse_actions(text)
        assert len(actions) == 1

    def test_parse_missing_target_xyz_raises(self) -> None:
        text = '[{"mass_kg": 0.5}]'
        with pytest.raises(ValueError, match="target_xyz"):
            _parse_actions(text)

    def test_parse_bad_target_xyz_raises(self) -> None:
        text = '[{"target_xyz": [0.3, 0.2]}]'
        with pytest.raises(ValueError, match="target_xyz"):
            _parse_actions(text)

    def test_parse_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            _parse_actions("not json at all")

    def test_parse_non_array_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON array"):
            _parse_actions('"just a string"')

    def test_parse_preserves_extra_fields(self) -> None:
        text = '[{"target_xyz": [0.3, 0.2, 0.1], "mass_kg": 1.2, "label": "mug"}]'
        actions = _parse_actions(text)
        assert actions[0]["mass_kg"] == 1.2
        assert actions[0]["label"] == "mug"


# ── _build_messages ──────────────────────────────────────────────────────


class TestBuildMessages:
    """Prompt construction."""

    def test_basic_message_structure(self) -> None:
        msgs = _build_messages("pick up the mug", [], "ur5e", 0.85, 5.0)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_system_prompt_contains_robot_info(self) -> None:
        msgs = _build_messages("task", [], "ur5e", 0.85, 5.0)
        system = msgs[0]["content"]
        assert "ur5e" in system
        assert "0.85" in system
        assert "5.0" in system

    def test_user_message_is_task(self) -> None:
        msgs = _build_messages("pick up the mug", [], "ur5e", 0.85, 5.0)
        assert msgs[1]["content"] == "pick up the mug"

    def test_constraints_appended_to_user_message(self) -> None:
        constraints = [
            Constraint(
                instruction="Move target within 0.85 m of base",
                reason="OUT_OF_REACH",
                fix_type="MOVE_TARGET",
                proposed_patch={"target_xyz": [0.3, 0.3, 0.3]},
            ),
        ]
        msgs = _build_messages("pick mug", constraints, "ur5e", 0.85, 5.0)
        user = msgs[1]["content"]
        assert "REJECTED" in user
        assert "OUT_OF_REACH" in user
        assert "Move target within 0.85 m" in user
        assert "0.3, 0.3, 0.3" in user

    def test_multiple_constraints(self) -> None:
        constraints = [
            Constraint(instruction="Fix reach", reason="OUT_OF_REACH"),
            Constraint(instruction="Fix payload", reason="OVER_PAYLOAD"),
        ]
        msgs = _build_messages("task", constraints, "ur5e", 0.85, 5.0)
        user = msgs[1]["content"]
        assert "1." in user
        assert "2." in user
        assert "OUT_OF_REACH" in user
        assert "OVER_PAYLOAD" in user

    def test_system_prompt_has_json_example(self) -> None:
        msgs = _build_messages("task", [], "ur5e", 0.85, 5.0)
        system = msgs[0]["content"]
        assert "target_xyz" in system
        assert "mass_kg" in system


# ── make_codegen_vla (mocked LLM) ───────────────────────────────────────


def _mock_llm_response(actions: list[dict]) -> str:
    """Create a JSON response string from action dicts."""
    return json.dumps(actions)


class TestMakeCodegenVla:
    """Factory function with mocked LLM calls."""

    def test_raises_without_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key"):
                make_codegen_vla()

    def test_creates_callable(self) -> None:
        vla = make_codegen_vla(api_key="test-key")
        assert callable(vla)

    def test_callable_returns_actions(self) -> None:
        expected = [{"target_xyz": [0.3, 0.2, 0.1], "mass_kg": 0.35}]

        with patch("axiom_tfg.codegen._call_llm", return_value=_mock_llm_response(expected)):
            vla = make_codegen_vla(api_key="test-key")
            actions = vla("pick up the mug", [])

        assert len(actions) == 1
        assert actions[0]["target_xyz"] == [0.3, 0.2, 0.1]

    def test_constraints_passed_to_prompt(self) -> None:
        """Verify constraints end up in the LLM messages."""
        captured_messages: list = []

        def capture_llm(messages, **kwargs):
            captured_messages.append(messages)
            return '[{"target_xyz": [0.3, 0.2, 0.1]}]'

        with patch("axiom_tfg.codegen._call_llm", side_effect=capture_llm):
            vla = make_codegen_vla(api_key="test-key")
            constraint = Constraint(
                instruction="Move target closer",
                reason="OUT_OF_REACH",
                proposed_patch={"target_xyz": [0.3, 0.3, 0.3]},
            )
            vla("pick mug", [constraint])

        user_msg = captured_messages[0][1]["content"]
        assert "OUT_OF_REACH" in user_msg
        assert "Move target closer" in user_msg

    def test_env_var_api_key(self) -> None:
        with patch.dict("os.environ", {"AXIOM_OPENAI_API_KEY": "env-key"}):
            vla = make_codegen_vla()  # no api_key arg
            assert callable(vla)

    def test_custom_model(self) -> None:
        captured: list = []

        def capture_llm(messages, *, model, **kwargs):
            captured.append(model)
            return '[{"target_xyz": [0.3, 0.2, 0.1]}]'

        with patch("axiom_tfg.codegen._call_llm", side_effect=capture_llm):
            vla = make_codegen_vla(api_key="k", model="gpt-4o")
            vla("task", [])

        assert captured[0] == "gpt-4o"


# ── prompt_and_resolve (full loop, mocked LLM) ──────────────────────────


class TestPromptAndResolve:
    """End-to-end: prompt → LLM → Axiom gates → resolve."""

    def test_resolved_on_first_try(self) -> None:
        """LLM generates a valid plan immediately."""
        response = _mock_llm_response([
            {"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35},
        ])

        with patch("axiom_tfg.codegen._call_llm", return_value=response):
            result = prompt_and_resolve(
                "pick up the mug",
                api_key="test-key",
            )

        assert result.resolved is True
        assert result.attempts == 1

    def test_resolved_after_retry(self) -> None:
        """LLM generates unreachable target first, then fixes it."""
        call_count = [0]

        def mock_llm(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt: unreachable
                return _mock_llm_response([
                    {"target_xyz": [5.0, 5.0, 5.0], "mass_kg": 0.35},
                ])
            # Second attempt: fixed
            return _mock_llm_response([
                {"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35},
            ])

        with patch("axiom_tfg.codegen._call_llm", side_effect=mock_llm):
            result = prompt_and_resolve(
                "pick up the mug",
                api_key="test-key",
            )

        assert result.resolved is True
        assert result.attempts == 2
        assert len(result.constraints) == 1

    def test_constraint_fed_back_to_llm(self) -> None:
        """Verify the fix instruction appears in the retry prompt."""
        captured_messages: list = []
        call_count = [0]

        def capture_llm(messages, **kwargs):
            captured_messages.append(messages)
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_llm_response([{"target_xyz": [5.0, 5.0, 5.0]}])
            return _mock_llm_response([{"target_xyz": [0.4, 0.2, 0.5]}])

        with patch("axiom_tfg.codegen._call_llm", side_effect=capture_llm):
            prompt_and_resolve("pick mug", api_key="test-key")

        # First call: no constraints in user message
        first_user = captured_messages[0][1]["content"]
        assert "REJECTED" not in first_user

        # Second call: constraint in user message
        second_user = captured_messages[1][1]["content"]
        assert "REJECTED" in second_user

    def test_unresolvable_exhausts_retries(self) -> None:
        """LLM keeps generating unreachable targets."""
        response = _mock_llm_response([
            {"target_xyz": [5.0, 5.0, 5.0]},
        ])

        with patch("axiom_tfg.codegen._call_llm", return_value=response):
            result = prompt_and_resolve(
                "pick from very far away",
                api_key="test-key",
                max_retries=2,
            )

        assert result.resolved is False
        assert result.attempts == 3  # 1 initial + 2 retries

    def test_multi_step_plan(self) -> None:
        """LLM generates a multi-step pick-and-place plan."""
        response = _mock_llm_response([
            {"target_xyz": [0.3, -0.2, 0.15], "mass_kg": 0.35},
            {"target_xyz": [0.4, 0.3, 0.4], "mass_kg": 0.35},
        ])

        with patch("axiom_tfg.codegen._call_llm", return_value=response):
            result = prompt_and_resolve(
                "pick up the mug and put it on the shelf",
                api_key="test-key",
            )

        assert result.resolved is True
        assert len(result.actions) == 2

    def test_keepout_zones_enforced(self) -> None:
        """Keepout zones are passed to the gates."""
        call_count = [0]

        def mock_llm(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_llm_response([{"target_xyz": [0.5, 0.5, 0.5]}])
            return _mock_llm_response([{"target_xyz": [0.1, 0.1, 0.1]}])

        with patch("axiom_tfg.codegen._call_llm", side_effect=mock_llm):
            result = prompt_and_resolve(
                "pick from the zone",
                api_key="test-key",
                keepout_zones=[
                    {"id": "cage", "min_xyz": [0.3, 0.3, 0.0], "max_xyz": [0.7, 0.7, 1.0]},
                ],
            )

        assert result.resolved is True
        assert result.attempts == 2
        assert result.constraints[0].reason == "IN_KEEP_OUT_ZONE"

    def test_payload_violation_and_fix(self) -> None:
        call_count = [0]

        def mock_llm(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_llm_response([
                    {"target_xyz": [0.4, 0.2, 0.3], "mass_kg": 50.0},
                ])
            return _mock_llm_response([
                {"target_xyz": [0.4, 0.2, 0.3], "mass_kg": 0.5},
            ])

        with patch("axiom_tfg.codegen._call_llm", side_effect=mock_llm):
            result = prompt_and_resolve(
                "pick up the heavy thing",
                api_key="test-key",
            )

        assert result.resolved is True
        assert result.constraints[0].reason == "OVER_PAYLOAD"

    def test_markdown_fenced_response_handled(self) -> None:
        """LLM wraps response in markdown fences — should still parse."""
        response = '```json\n[{"target_xyz": [0.4, 0.2, 0.5]}]\n```'

        with patch("axiom_tfg.codegen._call_llm", return_value=response):
            result = prompt_and_resolve(
                "pick up the mug",
                api_key="test-key",
            )

        assert result.resolved is True


# ── Top-level imports ────────────────────────────────────────────────────


class TestImports:

    def test_import_prompt_and_resolve(self) -> None:
        from axiom_tfg import prompt_and_resolve
        assert callable(prompt_and_resolve)

    def test_import_make_codegen_vla(self) -> None:
        from axiom_tfg import make_codegen_vla
        assert callable(make_codegen_vla)
