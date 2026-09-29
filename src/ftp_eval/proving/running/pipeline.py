"""Run benchmark preflight, evaluate each answer, then checkpoint the aggregate report."""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Sequence
from ...spec.benchmark import Benchmark
from ...spec.artifacts import ArtifactWriter
from ...backends.types import ModuleSource
from ..checking.policy import ContestPolicy
from .recording import grade_answer
from .inputs import Submission, snapshot_benchmark
from .manifest import policy_from_manifest
from .environment import observed_toolchain, reconcile_toolchain, validate_environment
from .run_directory import RunDirectory
from .results import ContestResult, GradedAnswer, ParticipantResult
from .summary import summarize_contest


def grade_contest(
    problem_set: Benchmark,
    submissions: Sequence[Submission],
    *,
    verifier: Any = None,
    timeout_s: float = 300.0,
    output_dir: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
    on_grade: Callable[[GradedAnswer], None] | None = None,
    policy: ContestPolicy | None = None,
    run_metadata: Mapping[str, Any] | None = None,
    writer: ArtifactWriter | None = None,
    strict_environment: bool = False,
) -> ContestResult:
    """Grade the whole benchmark and write the results directory."""
    problem_set = snapshot_benchmark(problem_set)
    if writer is not None and output_dir is not None:
        raise ValueError("choose writer or output_dir, not both")
    policy = policy or policy_from_manifest(problem_set.manifest)
    interfaces = problem_set.manifest.raw.get("problems", {})
    if not isinstance(interfaces, dict):
        raise ValueError("benchmark problems must be an object keyed by problem id")
    run_directory: ArtifactWriter | None = writer or (
        RunDirectory.create(output_dir, run_id) if output_dir is not None else None)
    result = ContestResult(
        problem_ids=problem_set.ids()
    )

    observed: dict[str, Any] = {}
    if run_directory is not None:
        run_directory.write_manifest(
            problems=problem_set.problems, participants=[s.participant for s in submissions],
            backend=getattr(verifier, "name", None), problem_metadata=problem_set.metadata,
            benchmark=problem_set.manifest, observed_toolchain=observed,
            toolchain_warnings=(),
            extra={"schema_version": 3, "grading": "frozen-interface", "status": "running",
                   "kernel_checked": False, "policy": policy.to_dict(),
                   "interfaces": interfaces, "run_metadata": dict(run_metadata or {}),
                   "timeout_s": timeout_s,
                   "unrecognized_files": {s.participant: list(s.unrecognized) for s in submissions}},
        )

    try:
        if run_directory is not None:
            run_directory.write_inputs(problem_set.problems, submissions)
            run_directory.write_run_stage("environment", "running")
        observed = observed_toolchain(verifier)
        if run_directory is not None:
            run_directory.set_state("running", toolchain={
                "declared": problem_set.manifest.to_dict()["declared_toolchain"],
                "observed": observed, "warnings": reconcile_toolchain(problem_set.manifest, observed)})
        strict_environment = strict_environment or getattr(verifier, "requires_strict_environment", False)
        environment_errors = validate_environment(problem_set.manifest, observed) if strict_environment else []
        capability = getattr(verifier, "supports_module_builds", None)
        if callable(capability):
            try:
                if not capability():
                    environment_errors.append("backend cannot compile/import frozen modules; benchmark grading is unavailable")
            except Exception as exc:
                environment_errors.append("module capability probe failed: %s" % exc)
        elif strict_environment and not callable(capability):
            environment_errors.append("official evaluation requires a verified module-build capability")
        preflight_modules = getattr(verifier, "preflight_modules", None)
        if callable(preflight_modules) and not environment_errors:
            try:
                planned = [ModuleSource(interfaces.get(pid, {}).get("module", "FtpEvalBench." + pid), source)
                           for pid, source in problem_set.problems.items()]
                planned.append(ModuleSource("FtpSubmission.Probe.Check", ""))
                preflight_modules(planned)
            except Exception as exc:
                environment_errors.append(str(exc))
        environment_result = {"strict": strict_environment, "errors": environment_errors,
                              "observed": observed}
        if run_directory is not None:
            run_directory.write_run_stage("environment", "failed" if environment_errors else "passed", environment_result)
            run_directory.set_state("running", environment_check=environment_result)
        if environment_errors:
            raise ValueError("strict environment preflight failed: " + "; ".join(environment_errors))
        for submission in submissions:
            participant = ParticipantResult(
                participant=submission.participant,
                unrecognized=submission.unrecognized,
                total_problems=len(problem_set),
            )
            for problem_id in sorted(submission.answers):
                config = interfaces.get(problem_id, {})
                if not isinstance(config, dict):
                    raise ValueError("%s: interface configuration must be an object" % problem_id)
                gold = config.get("gold_arguments", [])
                if not isinstance(gold, list) or not all(isinstance(x, str) for x in gold):
                    raise ValueError("%s: gold_arguments must be a list of Lean expressions" % problem_id)
                graded = grade_answer(
                    problem_set.problems[problem_id],
                    submission.answers[problem_id],
                    problem_id=problem_id,
                    participant=submission.participant,
                    verifier=verifier,
                    timeout_s=timeout_s,
                    run_directory=run_directory,
                    module=config.get("module", "FtpEvalBench." + problem_id),
                    gold_arguments=gold,
                    policy=policy,
                )
                participant.grades.append(graded)
                if run_directory is not None:
                    run_directory.write_answers([graded])
                if on_grade is not None:
                    on_grade(graded)
            result.participants.append(participant)

        if run_directory is not None:
            run_directory.write_run_stage("report", "running")
        # -- report: everything, refused answers included ----------------
        result.statistics = summarize_contest(
            result.grades(),
            problem_ids=problem_set.ids(),
            participants=len(submissions),
        )

        result.kernel_checked = any(g.kernel_checked for g in result.grades())

        # Reconciled whether or not a results folder is written: an unverified
        # toolchain is a fact about the run, not about its output.
        observed = observed_toolchain(verifier)
        result.toolchain_warnings = tuple(reconcile_toolchain(problem_set.manifest, observed))
        result.problems_without_provenance = problem_set.without_provenance()

        if run_directory is not None:
            run_directory.write_report(
                result.statistics,
                leaderboard=[p.to_dict() for p in result.leaderboard()],
                graded=result.grades(),
            )
            run_directory.write_run_stage("report", "passed", result.statistics.to_dict())
            result.run_directory = run_directory.directory
    except BaseException as exc:
        if run_directory is not None:
            run_directory.set_state("interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed", error=str(exc))
        raise
    if run_directory is not None:
        run_directory.set_state("complete", kernel_checked=result.kernel_checked)
    return result


evaluate_benchmark = grade_contest
