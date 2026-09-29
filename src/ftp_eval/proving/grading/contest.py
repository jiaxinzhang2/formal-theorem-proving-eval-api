"""Load a benchmark and grade answer groups against frozen targets.

Persist inputs and each interface/kernel/axiom step before advancing.
Missing answers, refusals and unrecognized files remain distinct."""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..interface import ContestPolicy, InterfaceFault, grade_interface, read_interface_problem, build_submission_modules, check_arguments, check_interface
from .artifacts import RunDirectory

from .metadata import (
    BenchmarkManifest,
    ProblemMetadata,
    load_benchmark_manifest,
    observed_toolchain,
    parse_problem_metadata,
    reconcile_toolchain,
    validate_environment,
)
from ...spec.benchmark import Benchmark, BenchmarkProblem
from ...spec.artifacts import ArtifactWriter
from ...backends.types import Diagnostic, Severity, Status, ModuleSource
from ..lean_file import parse_lean_file
from ..analysis.measure import proof_metrics
from ..analysis.modes import classify_failure, classify_success
from .results import GradedAnswer
from .statistics import ContestStatistics, summarize_contest

__all__ = [
    "ProblemSet",
    "Submission",
    "AnswerGrade",
    "ParticipantResult",
    "ContestResult",
    "load_problem_set",
    "load_submissions",
    "grade_contest",
    "evaluate_benchmark",
]

#: Files in a submission directory that are not answers.
_IGNORED = {".gitkeep", ".ds_store", "readme.md", "readme.txt", "notes.md"}

#: Kept as an alias: one graded answer is exactly a ``GradedAnswer``.
AnswerGrade = GradedAnswer


@dataclass(frozen=True)
class ProblemSet(Benchmark):
    """The setter's benchmark: a folder of Lean files, one frozen Problem.Target each."""

    problems: Mapping[str, str]
    root: Path | None = None
    #: Per-problem provenance, read from each file's metadata block.
    metadata: Mapping[str, ProblemMetadata] = field(default_factory=dict)
    #: Benchmark-wide declaration from ``benchmark.json``, when present.
    manifest: BenchmarkManifest = field(default_factory=BenchmarkManifest)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.problems))

    # -- the Benchmark contract ----------------------------------------

    @property
    def name(self) -> str:
        return self.manifest.name or (self.root.name if self.root else "")

    def problem_ids(self) -> tuple[str, ...]:
        return self.ids()

    def problem(self, problem_id: str) -> BenchmarkProblem:
        """One problem, with everything needed to cite it later.

        The content hash comes from the source, so a published result ties
        to the exact text that was graded rather than to a filename that
        may have been edited since.
        """
        entry = self.metadata.get(problem_id)
        return BenchmarkProblem(
            problem_id=problem_id,
            source=self.problems[problem_id],
            target="Problem.Target",
            metadata=entry,
            origin=str(self.root / ("%s.lean" % problem_id)) if self.root else "",
        )

    def manifest_fields(self) -> Mapping[str, Any]:
        return {**self.manifest.raw, "name": self.manifest.name,
                "version": self.manifest.version, "description": self.manifest.description,
                "language": self.manifest.language,
                "toolchain": {**self.manifest.raw.get("toolchain", {}),
                              "lean": self.manifest.lean, "mathlib_rev": self.manifest.mathlib_rev}}

    def source_for(self, problem_id: str) -> str:
        """The problem file's Lean source."""
        return self.problems[problem_id]

    def prose_for(self, problem_id: str) -> str:
        """The problem's natural-language statement, or "" if it records none.

        Empty is a normal answer, not an error: grading never needs prose.
        Only ``ftp-eval audit`` does, and it reports which problems it could
        not judge rather than failing on them.
        """
        entry = self.metadata.get(problem_id)
        return entry.prose if entry else ""

    def without_provenance(self) -> tuple[str, ...]:
        """Problems recording neither a MathDB id nor a source.

        Reported rather than rejected: an unsourced problem still grades.
        But for a published contest it is worth knowing which ones cannot
        be traced back.
        """
        return tuple(
            pid
            for pid in self.ids()
            if not (pid in self.metadata and self.metadata[pid].has_provenance)
        )

    def __len__(self) -> int:
        return len(self.problems)


@dataclass(frozen=True)
class Submission:
    """One participant's answers, keyed by problem id."""

    participant: str
    answers: Mapping[str, str]
    #: Files matching no problem id. Reported, never dropped.
    unrecognized: tuple[str, ...] = ()
    root: Path | None = None


@dataclass
class ParticipantResult:
    """One participant's result."""

    participant: str
    grades: list[GradedAnswer] = field(default_factory=list)
    unrecognized: tuple[str, ...] = ()
    total_problems: int = 0

    @property
    def solved(self) -> int:
        return sum(1 for g in self.grades if g.solved)

    @property
    def attempted(self) -> int:
        return len(self.grades)

    @property
    def not_attempted(self) -> int:
        return max(0, self.total_problems - self.attempted)

    @property
    def rejected(self) -> int:
        return sum(1 for g in self.grades if not g.solved)

    @property
    def flagged(self) -> tuple[GradedAnswer, ...]:
        """Refused for a reason worth a human look.

        "Stated the theorem but did not prove it" is an honest miss and is
        excluded; everything else -- a changed statement, a gutted
        definition, reward hacking -- is not.
        """
        return tuple(
            g
            for g in self.grades
            if not g.solved
            and not (g.report and g.report.honest_miss)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant": self.participant,
            "solved": self.solved,
            "attempted": self.attempted,
            "not_attempted": self.not_attempted,
            "rejected": self.rejected,
            "flagged": len(self.flagged),
            "unrecognized_files": list(self.unrecognized),
        }


@dataclass
class ContestResult:
    """The whole run."""

    participants: list[ParticipantResult] = field(default_factory=list)
    problem_ids: tuple[str, ...] = ()
    kernel_checked: bool = False
    statistics: ContestStatistics = field(default_factory=ContestStatistics)
    #: Discrepancies between the benchmark's declared toolchain and the one
    #: that actually ran. Warnings, not errors: a deliberate re-run against
    #: a newer Mathlib is legitimate, but it must not go unrecorded.
    toolchain_warnings: tuple[str, ...] = ()
    #: Problems with no MathDB id and no source.
    problems_without_provenance: tuple[str, ...] = ()
    #: Where the results were written, when they were.
    run_directory: Path | None = None

    def grades(self) -> list[GradedAnswer]:
        return [g for p in self.participants for g in p.grades]

    def leaderboard(self) -> list[ParticipantResult]:
        return sorted(
            self.participants, key=lambda p: (-p.solved, p.attempted, p.participant)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "problems": len(self.problem_ids),
            "participants": len(self.participants),
            "kernel_checked": self.kernel_checked,
            "run_directory": str(self.run_directory) if self.run_directory else None,
            "leaderboard": [p.to_dict() for p in self.leaderboard()],
            "statistics": self.statistics.to_dict(),
        }

    def format_text(self) -> str:
        lines = [
            "benchmark: %d problem(s), %d participant(s)%s"
            % (
                len(self.problem_ids),
                len(self.participants),
                "" if self.kernel_checked else "   [kernel checks not run]",
            ),
            "",
            "%-4s %-24s %7s %9s %9s %s"
            % ("#", "participant", "solved", "attempted", "rejected", "to review"),
        ]
        for index, participant in enumerate(self.leaderboard(), start=1):
            lines.append(
                "%-4d %-24s %7d %9d %9d %s"
                % (
                    index,
                    participant.participant[:24],
                    participant.solved,
                    participant.attempted,
                    participant.rejected,
                    len(participant.flagged) or "",
                )
            )
        lines.append("")
        lines.append(self.statistics.format_text())
        stray = [(p.participant, f) for p in self.participants for f in p.unrecognized]
        if stray:
            lines.append("")
            lines.append("unrecognized files (NOT graded, %d):" % len(stray))
            for participant_name, filename in stray[:10]:
                lines.append("  %s/%s" % (participant_name, filename))
        if self.toolchain_warnings:
            lines.append("")
            lines.append("TOOLCHAIN:")
            for warning in self.toolchain_warnings:
                lines.append("  %s" % warning)
        if self.problems_without_provenance:
            lines.append("")
            lines.append(
                "%d problem(s) record no MathDB id and no source: %s%s"
                % (
                    len(self.problems_without_provenance),
                    ", ".join(self.problems_without_provenance[:10]),
                    " ..." if len(self.problems_without_provenance) > 10 else "",
                )
            )
        if self.run_directory:
            lines.append("")
            lines.append("results written to %s" % self.run_directory)
        return "\n".join(lines)


def load_problem_set(root: str | os.PathLike[str], *, suffix: str = ".lean") -> ProblemSet:
    """Load the benchmark. The file stem is the problem id."""
    directory = Path(root)
    if not directory.is_dir():
        raise NotADirectoryError("problem directory not found: %s" % directory)
    problems = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*%s" % suffix))
    }
    if not problems:
        raise ValueError("no %s files in %s" % (suffix, directory))
    metadata = {
        pid: parse_problem_metadata(source, problem_id=pid)
        for pid, source in problems.items()
    }
    # `benchmark.json` sits beside the problems directory or inside it.
    manifest = load_benchmark_manifest(directory)
    if not manifest.name:
        manifest = load_benchmark_manifest(directory.parent)
    return ProblemSet(
        problems=problems, root=directory, metadata=metadata, manifest=manifest
    )


def load_submissions(
    root: str | os.PathLike[str], problem_set: ProblemSet, *, suffix: str = ".lean"
) -> list[Submission]:
    """Load one directory per participant.

    A file whose stem is not a problem id is recorded as unrecognized
    rather than dropped: a participant who misnames a file should be told,
    not silently scored zero on work they did.
    """
    directory = Path(root)
    if not directory.is_dir():
        raise NotADirectoryError("submission directory not found: %s" % directory)

    out: list[Submission] = []
    for participant_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        answers: dict[str, str] = {}
        unrecognized: list[str] = []
        for path in sorted(participant_dir.iterdir()):
            if path.is_dir() or path.name.lower() in _IGNORED:
                continue
            if path.suffix == suffix and path.stem in problem_set.problems:
                answers[path.stem] = path.read_text(encoding="utf-8")
            else:
                unrecognized.append(path.name)
        out.append(
            Submission(
                participant=participant_dir.name,
                answers=answers,
                unrecognized=tuple(unrecognized),
                root=participant_dir,
            )
        )
    return out


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

    # Which failure this was, in the vocabulary the proof-side analysis
    # uses. Reward hacking is its own status so it does not get filed as a
    # generic failure -- the distinction is the whole point of stage 1.
    hacked = bool(
        (graded.report and any(f is InterfaceFault.REWARD_HACKING for f, _ in graded.report.faults))
        or (graded.axiom_audit and not graded.axiom_audit.ok)
    )
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


def snapshot_benchmark(benchmark: Benchmark) -> ProblemSet:
    """Read the public Benchmark contract once; freeze a run's inputs in memory."""
    fields = dict(benchmark.manifest_fields())
    toolchain = fields.get("toolchain", {})
    if not isinstance(toolchain, dict):
        raise ValueError("benchmark toolchain must be an object")
    manifest = BenchmarkManifest(name=str(fields.get("name") or benchmark.name),
                                 version=str(fields.get("version") or ""),
                                 description=str(fields.get("description") or ""),
                                 language=str(fields.get("language") or "lean4"),
                                 lean=str(toolchain.get("lean") or ""),
                                 mathlib_rev=str(toolchain.get("mathlib_rev") or ""), raw=fields)
    problems, metadata = {}, {}
    for item in benchmark.iter_problems():
        if item.problem_id in problems:
            raise ValueError("duplicate problem id: %s" % item.problem_id)
        if item.target != "Problem.Target":
            raise ValueError("benchmark must export the frozen Problem.Target interface")
        problems[item.problem_id] = item.source
        metadata[item.problem_id] = item.metadata if isinstance(item.metadata, ProblemMetadata) else parse_problem_metadata(item.source, problem_id=item.problem_id)
    return ProblemSet(problems, metadata=metadata, manifest=manifest)


evaluate_benchmark = grade_contest


def policy_from_manifest(manifest: BenchmarkManifest) -> ContestPolicy:
    """Load explicit benchmark policy; reject misspelled or malformed knobs."""
    data = manifest.raw.get("policy", {})
    if not isinstance(data, dict):
        raise ValueError("benchmark policy must be an object")
    unknown = set(data) - {"allowed_imports", "allowed_axioms", "allow_global_instances", "isolate_builds", "require_replay"}
    if unknown:
        raise ValueError("unknown policy fields: %s" % ", ".join(sorted(unknown)))
    fields = dict(data)
    for name in ("allowed_imports", "allowed_axioms"):
        if name in fields:
            values = fields[name]
            if not isinstance(values, list) or not all(isinstance(x, str) and x for x in values):
                raise ValueError("%s must be a list of non-empty names" % name)
            fields[name] = tuple(values) if name == "allowed_imports" else frozenset(values)
    for name in ("allow_global_instances", "isolate_builds", "require_replay"):
        if name in fields and not isinstance(fields[name], bool):
            raise ValueError("%s must be a boolean" % name)
    return ContestPolicy(**fields)
