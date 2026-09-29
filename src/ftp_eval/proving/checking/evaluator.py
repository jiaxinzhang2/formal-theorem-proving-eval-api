"""Evaluate one submission through screening, compilation, replay and axiom policy."""
from __future__ import annotations

from typing import Any, Callable, Mapping
from ...backends.types import Diagnostic, ModuleBuild, Severity, Status
from ..lean_file import parse_lean_file
from .interface import CHECK_THEOREM, InterfaceFault, InterfaceProblem, InterfaceReport, InterfaceVerdict
from .policy import ContestPolicy
from .screening import check_arguments, check_interface
from .modules import build_submission_modules


def grade_interface(
    problem: InterfaceProblem,
    problem_source: str,
    answer_source: str,
    *,
    verifier: Any = None,
    participant: str = "",
    policy: ContestPolicy | None = None,
    answer_module: str = "",
    timeout_s: float = 300.0,
    on_stage: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> InterfaceVerdict:
    """Text screen, then the kernel, then the axiom listing. Never raises.

    The order is cheapest-first, and the first two are gates: an answer that
    does not present the interface is not compiled, and one the kernel
    rejects is not audited. What is *not* a gate is the audit -- it runs on
    an accepted proof precisely because acceptance is not the bar.
    """
    policy = policy or ContestPolicy()
    verdict = InterfaceVerdict(problem_id=problem.problem_id, participant=participant)

    def record(stage: str, status: str, payload: Any = None, detail: str = "") -> None:
        event = {"stage": stage, "status": status, "payload": payload, "detail": detail}
        verdict.stages.append(event)
        if on_stage is not None:
            on_stage(stage, event)

    record("interface", "running")
    try:
        report = check_interface(problem, answer_source, allowed_imports=policy.allowed_imports)
        if not policy.allow_global_instances:
            instances = [d for d in parse_lean_file(answer_source).declarations if d.kind == "instance"]
            if instances:
                report.faults += ((InterfaceFault.INSTANCE_NOT_ALLOWED, "benchmark policy forbids global instances"),)
    except Exception as exc:
        report = InterfaceReport(problem.problem_id, faults=((InterfaceFault.UNPARSED, str(exc)),))
    verdict.report = report
    record("interface", "passed" if report.ok else "failed", report.to_dict())
    if not report.ok or verifier is None:
        why = "interface refused" if not report.ok else "no prover configured"
        record("kernel", "not_run", detail=why)
        for stage in ("replay", "dependencies", "axioms"):
            record(stage, "not_run", detail=why)
        return verdict

    arguments, against_gold = check_arguments(problem, report)
    verdict.against_gold = against_gold
    module = answer_module or "%s.Submission.%s" % (
        problem.module.split(".")[0] or "FtpEvalBench", participant or "answer",
    )
    modules = build_submission_modules(problem, problem_source, answer_source, module, arguments)

    record("kernel", "running")

    def backend_stage(stage: str, event: Mapping[str, Any]) -> None:
        if stage not in ("kernel", "replay", "dependencies"):
            raise ValueError("unknown backend stage: %s" % stage)
        record(stage, str(event["status"]), event.get("payload"), str(event.get("detail", "")))

    try:
        build = verifier.build_modules(
            modules,
            audit_declaration=CHECK_THEOREM, timeout_s=timeout_s, on_stage=backend_stage,
        )
    except Exception as exc:
        build = ModuleBuild(Status.ERROR, diagnostics=(Diagnostic(Severity.ERROR, str(exc)),))
    verdict.build = build
    status = "not_run" if build is None or build.status in (Status.ERROR, Status.SKIPPED) else (
        "passed" if build.verified else "failed"
    )
    if not any(e["stage"] == "kernel" and e["status"] != "running" for e in verdict.stages):
        record("kernel", status, build.to_dict() if build else None,
               "module builds unavailable" if build is None else "")
    for stage in ("replay", "dependencies"):
        if not any(e["stage"] == stage and e["status"] != "running" for e in verdict.stages):
            # Legacy adapters may return an axiom listing in one response,
            # but cannot claim separately observed stage execution.
            record(stage, "not_run", detail="backend did not report this phase")
    replay_passed = any(e["stage"] == "replay" and e["status"] == "passed" for e in verdict.stages)
    if ((policy.require_replay or getattr(verifier, "requires_replay", False))
            and not replay_passed and build is not None and build.verified):
        build.status = Status.ERROR
        build.raw = {**build.raw, "failed_stage": "replay"}
        build.diagnostics += (Diagnostic(Severity.ERROR, "required kernel replay did not pass"),)
    if build is not None and build.verified:
        record("axioms", "running")
        verdict.axiom_audit = policy.audit(build.axioms)
        record("axioms", "passed" if verdict.axiom_audit.ok else "failed", verdict.axiom_audit.to_dict())
    else:
        record("axioms", "not_run", detail="kernel check did not pass")
    return verdict


# Explicit public name; retained grade_interface spelling is source compatible.
evaluate_submission = grade_interface
