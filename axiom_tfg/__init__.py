"""axiom-tfg: deterministic physical task feasibility gate linter."""

__version__ = "0.1.0"

from axiom_tfg.codegen import make_codegen_vla, prompt_and_resolve
from axiom_tfg.resolve import Attempt, Constraint, ResolveResult, resolve
from axiom_tfg.robots import ROBOT_REGISTRY, RobotProfile, get_robot
from axiom_tfg.sdk import Result, check, check_simple
from axiom_tfg.vla import ActionResult, PlanResult, validate_action, validate_plan

# Audit (lazy — heavy deps like datasets/pyarrow are optional)
from axiom_tfg.audit import AuditConfig, AuditReport, audit_trajectory

__all__ = [
    "check",
    "check_simple",
    "Result",
    "validate_action",
    "validate_plan",
    "ActionResult",
    "PlanResult",
    "resolve",
    "Constraint",
    "ResolveResult",
    "Attempt",
    "prompt_and_resolve",
    "make_codegen_vla",
    "RobotProfile",
    "ROBOT_REGISTRY",
    "get_robot",
    "AuditConfig",
    "AuditReport",
    "audit_trajectory",
    "__version__",
]
