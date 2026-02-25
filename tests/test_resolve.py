"""Tests for the closed-loop VLA resolver."""

from __future__ import annotations

from axiom_tfg.resolve import Constraint, ResolveResult, resolve


class TestResolveFirstTry:
    """VLA proposes a valid action — resolves immediately."""

    def test_resolved_immediately(self) -> None:
        def vla(task, constraints):
            return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.35}]

        r = resolve(vla, "pick up the mug")
        assert r.resolved is True
        assert r.attempts == 1
        assert len(r.constraints) == 0
        assert len(r.history) == 1

    def test_no_constraints_passed_on_first_call(self) -> None:
        def vla(task, constraints):
            assert len(constraints) == 0
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick")
        assert r.resolved is True


class TestResolveWithRetry:
    """VLA fails first, adapts after receiving constraints."""

    def test_adapts_after_one_retry(self) -> None:
        calls: list[list[Constraint]] = []

        def vla(task, constraints):
            calls.append(list(constraints))
            if not constraints:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]  # unreachable
            return [{"target_xyz": [0.4, 0.2, 0.5]}]  # fixed

        r = resolve(vla, "pick up the mug")
        assert r.resolved is True
        assert r.attempts == 2
        assert len(r.constraints) == 1
        assert len(calls) == 2
        # First call: no constraints
        assert len(calls[0]) == 0
        # Second call: one constraint
        assert len(calls[1]) == 1
        assert isinstance(calls[1][0], Constraint)

    def test_constraint_has_structured_data(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick")
        c = r.constraints[0]
        assert c.instruction  # non-empty string
        assert c.reason  # non-empty reason code
        assert c.proposed_patch is not None  # has structured fix data

    def test_vla_uses_proposed_patch_coordinates(self) -> None:
        """VLA reads the proposed_patch and uses exact coordinates."""

        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]
            patch = constraints[-1].proposed_patch
            if patch and "target_xyz" in patch:
                return [{"target_xyz": patch["target_xyz"]}]
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick")
        assert r.resolved is True
        assert r.attempts == 2

    def test_payload_fix_fed_back(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 100.0}]
            return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.5}]

        r = resolve(vla, "pick heavy thing")
        assert r.resolved is True
        assert r.attempts == 2
        assert r.constraints[0].reason == "OVER_PAYLOAD"

    def test_keepout_fix_fed_back(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [0.5, 0.5, 0.5]}]
            return [{"target_xyz": [0.1, 0.1, 0.1]}]

        r = resolve(
            vla,
            "pick",
            keepout_zones=[
                {"id": "box", "min_xyz": [0.3, 0.3, 0.0], "max_xyz": [0.7, 0.7, 1.0]},
            ],
        )
        assert r.resolved is True
        assert r.attempts == 2
        assert r.constraints[0].reason == "IN_KEEP_OUT_ZONE"

    def test_multiple_retries_accumulate_constraints(self) -> None:
        call_count = [0]

        def vla(task, constraints):
            call_count[0] += 1
            if call_count[0] < 3:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick", max_retries=5)
        assert r.resolved is True
        assert r.attempts == 3
        assert len(r.constraints) == 2  # two failures before success


class TestResolveMaxRetries:
    """VLA never finds a valid action — exhausts retries."""

    def test_gives_up_after_max_retries(self) -> None:
        calls = [0]

        def vla(task, constraints):
            calls[0] += 1
            return [{"target_xyz": [5.0, 5.0, 5.0]}]

        r = resolve(vla, "pick", max_retries=3)
        assert r.resolved is False
        assert r.attempts == 4  # 1 initial + 3 retries
        assert calls[0] == 4

    def test_custom_max_retries(self) -> None:
        calls = [0]

        def vla(task, constraints):
            calls[0] += 1
            return [{"target_xyz": [5.0, 5.0, 5.0]}]

        r = resolve(vla, "pick", max_retries=1)
        assert r.resolved is False
        assert r.attempts == 2
        assert calls[0] == 2

    def test_zero_retries_means_one_attempt(self) -> None:
        calls = [0]

        def vla(task, constraints):
            calls[0] += 1
            return [{"target_xyz": [5.0, 5.0, 5.0]}]

        r = resolve(vla, "pick", max_retries=0)
        assert r.resolved is False
        assert r.attempts == 1
        assert calls[0] == 1


class TestResolvePlan:
    """Multi-step plan resolution."""

    def test_plan_resolved_after_retry(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [
                    {"target_xyz": [0.4, 0.2, 0.5]},
                    {"target_xyz": [5.0, 5.0, 5.0]},  # bad step
                ]
            return [
                {"target_xyz": [0.4, 0.2, 0.5]},
                {"target_xyz": [0.3, -0.1, 0.6]},  # fixed
            ]

        r = resolve(vla, "pick and place")
        assert r.resolved is True
        assert r.attempts == 2
        assert len(r.actions) == 2

    def test_empty_plan_resolves(self) -> None:
        def vla(task, constraints):
            return []

        r = resolve(vla, "nothing")
        assert r.resolved is True
        assert r.attempts == 1


class TestResolveSingleDict:
    """VLA returns a single dict instead of a list."""

    def test_single_dict_normalized(self) -> None:
        def vla(task, constraints):
            return {"target_xyz": [0.4, 0.2, 0.5]}  # not wrapped in list

        r = resolve(vla, "pick")
        assert r.resolved is True
        assert r.attempts == 1
        assert len(r.actions) == 1


class TestResolveHistory:
    """History tracking across attempts."""

    def test_history_records_all_attempts(self) -> None:
        call_count = [0]

        def vla(task, constraints):
            call_count[0] += 1
            if call_count[0] < 3:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick", max_retries=5)
        assert len(r.history) == 3
        # First two failed
        assert not r.history[0].result.allowed
        assert not r.history[1].result.allowed
        # Third succeeded
        assert r.history[2].result.allowed
        # First two added constraints
        assert r.history[0].constraint_added is not None
        assert r.history[1].constraint_added is not None
        # Third didn't
        assert r.history[2].constraint_added is None

    def test_history_preserves_actions(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick")
        assert r.history[0].actions == [{"target_xyz": [5.0, 5.0, 5.0]}]
        assert r.history[1].actions == [{"target_xyz": [0.4, 0.2, 0.5]}]

    def test_final_result_matches_last_attempt(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick")
        assert r.final_result is r.history[-1].result


class TestResolveRobotKwargs:
    """Robot parameters forwarded to gates."""

    def test_custom_payload_limit(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 3.0}]
            return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.5}]

        r = resolve(vla, "pick", max_payload_kg=1.0)
        assert r.resolved is True
        assert r.attempts == 2
        assert r.constraints[0].reason == "OVER_PAYLOAD"


class TestConstraintFields:
    """Constraint dataclass field coverage."""

    def test_reachability_constraint(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [5.0, 5.0, 5.0]}]
            return [{"target_xyz": [0.4, 0.2, 0.5]}]

        r = resolve(vla, "pick")
        c = r.constraints[0]
        assert isinstance(c.instruction, str)
        assert len(c.instruction) > 0
        assert isinstance(c.reason, str)
        assert c.fix_type is not None  # e.g. "MOVE_TARGET"
        assert isinstance(c.proposed_patch, dict)

    def test_payload_constraint(self) -> None:
        def vla(task, constraints):
            if not constraints:
                return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 100.0}]
            return [{"target_xyz": [0.4, 0.2, 0.5], "mass_kg": 0.5}]

        r = resolve(vla, "pick")
        c = r.constraints[0]
        assert c.reason == "OVER_PAYLOAD"
        assert c.fix_type is not None


class TestImports:
    """Top-level package exports."""

    def test_import_resolve(self) -> None:
        from axiom_tfg import resolve

        assert callable(resolve)

    def test_import_constraint(self) -> None:
        from axiom_tfg import Constraint

        assert Constraint is not None

    def test_import_resolve_result(self) -> None:
        from axiom_tfg import ResolveResult

        assert ResolveResult is not None

    def test_import_attempt(self) -> None:
        from axiom_tfg import Attempt

        assert Attempt is not None
