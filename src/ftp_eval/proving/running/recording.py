"""Checkpoint one checking verdict and attach benchmark-report measurements.

Acceptance is delegated to checking.evaluator. This adapter preserves input
modules and stage events, then describes the returned verdict with metrics;
it does not define a second acceptance rule.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Mapping, Sequence
from ...spec.artifacts import ArtifactWriter
from ...backends.types import Diagnostic, ModuleSource, Severity, Status
from ...backends.soundness import HackClass, parse_label
from ..checking.policy import ContestPolicy
from ..checking.interface import InterfaceFault
from ..checking.evaluator import grade_interface
from ..checking.screening import read_interface_problem, check_arguments, check_interface
from ..checking.modules import build_submission_modules
from ..lean_file import parse_lean_file
from ..analysis.metrics import proof_metrics
from ..analysis.classification import FailureMode, classify_failure, classify_success
from .results import GradedAnswer


def grade_answer(
    problem_source: str,
    answer_source: str,
    *,
    problem_id: str,
    participant: str = "",
    verifier: Any = None,
    module: str = "",
    gold_arguments: Sequence[str] = (),
    policy: ContestPolicy | None = None,
    timeout_s: float = 300.0,
    run_directory: ArtifactWriter | None = None,
) -> GradedAnswer:
    """Check the interface, the frozen target's type, and its axiom closure."""
    problem = read_interface_problem(
        problem_source, problem_id=problem_id, module=module or "FtpEvalBench." + problem_id
    )
    problem = replace(problem, gold_arguments=tuple(gold_arguments))
    ModuleSource(problem.module, problem_source)  # Validate before saving module paths.
    if problem.gold_arguments and len(problem.gold_arguments) != len(problem.target_parameters):
        raise ValueError("%s: gold_arguments must match Target's parameter count" % problem_id)
    answer_module = "FtpSubmission.A" + hashlib.sha256(
        (problem_id + "\0" + participant).encode("utf-8")
    ).hexdigest()[:20]
    # Save the exact inputs before any backend call can fail or be interrupted.
    prepared = GradedAnswer(problem_id=problem_id, participant=participant)
    prepared.modules = {problem.module: problem_source, answer_module: answer_source}
    if run_directory is not None:
        run_directory.write_modules(prepared)

    def checkpoint(stage: str, event: Mapping[str, Any]) -> None:
        if run_directory is not None:
            if stage == "kernel" and event["status"] == "running":
                report = check_interface(problem, answer_source)
                arguments, _ = check_arguments(problem, report)
                prepared.modules = {m.module: m.source for m in build_submission_modules(problem, problem_source, answer_source, answer_module, arguments)}
                run_directory.write_modules(prepared)
            run_directory.write_stage(problem_id, participant, stage, event)

    verdict = grade_interface(
        problem, problem_source, answer_source, verifier=verifier,
        participant=participant, policy=policy, answer_module=answer_module,
        timeout_s=timeout_s, on_stage=checkpoint,
    )
    graded = GradedAnswer(**vars(verdict))
    assert verdict.report is not None
    arguments, _ = check_arguments(problem, verdict.report)
    graded.modules = {m.module: m.source for m in build_submission_modules(problem, problem_source, answer_source, answer_module, arguments)}
    diagnostics = tuple(d.message for d in verdict.build.diagnostics) if verdict.build else ()
    _attach_metrics(graded, problem_source, answer_source, diagnostics)
    if run_directory is not None:
        run_directory.write_modules(graded)
    return graded


def _answer_proof(answer_source: str, target: str) -> str:
    """The target theorem's proof body, or the whole file if it has none.

    A refused answer may have no recognizable target -- that is often why
    it was refused -- and its shape is still worth measuring, so the
    fallback records something rather than nothing.
    """
    try:
        parsed = parse_lean_file(answer_source)
    except Exception:  # pragma: no cover - a parse failure is not fatal here
        return answer_source
    for declaration in parsed.statements():
        if not target or declaration.name == target or declaration.qualified_name == target:
            return declaration.body or answer_source
    return answer_source


def _attach_metrics(
    graded: GradedAnswer,
    problem_source: str,
    answer_source: str,
    compile_diagnostics: Sequence[str] = (),
) -> None:
    """Measure the answer, whatever stage it stopped at.

    The prover's own messages are passed through so the failure mode is
    specific -- "unsolved goals" or "timeout" rather than "unclassified",
    which is the difference between a usable breakdown and a useless one.
    """
    target = "Submission.solution"
    parsed_problem = parse_lean_file(problem_source)
    frozen = next((d for d in parsed_problem.declarations if d.qualified_name == "Problem.Target"), None)
    statement = ""
    if frozen is not None and frozen.body.strip() and not frozen.body.lstrip().startswith("by"):
        # Measure the proposition itself, with its binders, rather than the module
        # or the result sort Prop. Unsupported tactic-defined targets stay absent.
        statement = frozen.signature.rsplit(":", 1)[0] + ": " + frozen.body
    graded.tactics, graded.structure = proof_metrics(
        _answer_proof(answer_source, target), statement
    )

    if graded.solved:
        graded.success_mode = classify_success(graded.tactics, graded.structure).value
        return

    # Classify observed mechanisms, never intent. Incomplete proofs remain
    # refused, including placeholders found only in the dependency closure.
    audit_labels = [parse_label(v) for v in graded.axiom_audit.violations] if graded.axiom_audit else []
    hacked = bool(
        (graded.report and any(f is InterfaceFault.REWARD_HACKING for f, _ in graded.report.faults))
        or any(kind != HackClass.PLACEHOLDER.value for kind, _ in audit_labels)
    )
    incomplete = bool(graded.report and graded.report.incomplete_proof) or any(
        pattern == "kernel.sorry_ax" for _, pattern in audit_labels
    )
    if incomplete and not hacked:
        graded.failure_mode = FailureMode.PLACEHOLDER_LEFT.value
        return
    if not hacked and any(pattern == "kernel.audit_missing" for _, pattern in audit_labels):
        graded.failure_mode = FailureMode.HARNESS_ERROR.value
        return
    mode = classify_failure(
        Status.REJECTED if hacked else Status.FAILED,
        None,
        tuple(
            Diagnostic(Severity.ERROR, message) for message in compile_diagnostics
        ),
        proof=_answer_proof(answer_source, target),
        soundness_ok=not hacked,
    )
    graded.failure_mode = mode.value if mode else ""
