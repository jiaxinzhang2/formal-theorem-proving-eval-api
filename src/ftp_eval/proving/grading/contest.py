"""The driver: a benchmark of N Lean problems, many participants.

Loads the problem set and the submissions, runs every answer through the
three stages, and writes one results directory. See
:mod:`ftp_eval.proving.grading` for the stage diagram and
:mod:`ftp_eval.proving.grading.artifacts` for the directory layout.

Three things this is built to get right, because a contest grader is
adversarial in a way an internal eval is not:

* **A missing answer, a wrong answer and an unparseable file are three
  different outcomes.** Collapsing them loses the distinction between a
  participant who skipped a problem and one whose work was misfiled.
* **An unrecognized file is never silently ignored.** A submission naming a
  problem that does not exist is reported, because dropping it would make
  a participant's work vanish with nobody noticing.
* **Nothing is graded twice or skipped.** The manifest reconciles: every
  problem, every participant, every answer accounted for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..matching import MatchStatus, MismatchKind
from .artifacts import RunDirectory

from .metadata import (
    BenchmarkManifest,
    ProblemMetadata,
    load_benchmark_manifest,
    observed_toolchain,
    parse_problem_metadata,
    reconcile_toolchain,
)
from ...spec.benchmark import Benchmark, BenchmarkProblem
from ...spec.stage import StageContext, StageId
from .pipeline import run_stages
from ...backends.types import Diagnostic, Severity, Status
from ..lean_file import parse_lean_file
from ..analysis.measure import proof_metrics
from ..analysis.modes import classify_failure, classify_success
from .stages import GradedAnswer, Stage
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
]

#: Files in a submission directory that are not answers.
_IGNORED = {".gitkeep", ".ds_store", "readme.md", "readme.txt", "notes.md"}

#: Kept as an alias: one graded answer is exactly a ``GradedAnswer``.
AnswerGrade = GradedAnswer


@dataclass(frozen=True)
class ProblemSet(Benchmark):
    """The setter's benchmark: a folder of Lean files, one theorem each."""

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
            target=entry.theorem_name or None if entry is not None else None,
            metadata=entry,
            origin=str(self.root / ("%s.lean" % problem_id)) if self.root else "",
        )

    def manifest_fields(self) -> Mapping[str, Any]:
        return self.manifest.to_dict()

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
            and not (g.match and g.match.status is MatchStatus.MATCHED_BUT_UNPROVED)
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
                "" if self.kernel_checked else "   [stage 2 skipped -- no prover]",
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
            for participant, filename in stray[:10]:
                lines.append("  %s/%s" % (participant, filename))
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
    target: str | None = None,
    timeout_s: float = 300.0,
    run_directory: RunDirectory | None = None,
) -> GradedAnswer:
    """Run one answer through stages 1 and 2.

    Stage 2 runs only if stage 1 passed. Compiling an answer that does not
    state the theorem is wasted work, and a compile that *succeeds* on the
    wrong theorem reads like a pass -- which is the confusion this whole
    pipeline exists to remove.
    """
    # Driven through the stage objects rather than inline, so there is one
    # code path and the ordering lives in each stage's `requires` rather
    # than here. See ftp_eval.proving.grading.pipeline.
    context = StageContext(
        problem_id=problem_id,
        problem_source=problem_source,
        answer_source=answer_source,
        participant=participant,
        target=target,
        verifier=verifier,
        timeout_s=timeout_s,
    )
    results = run_stages(context)

    graded = GradedAnswer(problem_id=problem_id, participant=participant)
    match_result = results.get(StageId.MATCH)
    if match_result is not None:
        graded.match = match_result.payload
        graded.stage_reached = Stage.MATCH
        graded.stage_status = match_result.status

    compile_diagnostics: tuple[str, ...] = ()
    compile_result = results.get(StageId.COMPILE)
    if compile_result is not None:
        graded.stage_reached = Stage.COMPILE
        graded.stage_status = compile_result.status
        graded.compile_status = compile_result.status
        graded.compile_detail = compile_result.detail
        graded.compile_time_s = compile_result.elapsed_s
        outcome = compile_result.payload
        compile_diagnostics = tuple(getattr(outcome, "diagnostics", ()))
        if run_directory is not None and getattr(outcome, "source", ""):
            run_directory.write_probe(
                graded, outcome.source, "\n".join(getattr(outcome, "diagnostics", ()))
            )

    _attach_metrics(graded, problem_source, answer_source, compile_diagnostics)
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
    target = graded.match.target if graded.match else ""
    statement = (graded.match.expected_signature if graded.match else "") or problem_source
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
        graded.match
        and any(m.kind is MismatchKind.REWARD_HACKING for m in graded.match.mismatches)
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
    problem_set: ProblemSet,
    submissions: Sequence[Submission],
    *,
    verifier: Any = None,
    timeout_s: float = 300.0,
    output_dir: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
    on_grade: Callable[[GradedAnswer], None] | None = None,
) -> ContestResult:
    """Grade the whole benchmark and write the results directory."""
    run_directory = (
        RunDirectory.create(output_dir, run_id) if output_dir is not None else None
    )
    result = ContestResult(
        problem_ids=problem_set.ids(), kernel_checked=verifier is not None
    )

    for submission in submissions:
        participant = ParticipantResult(
            participant=submission.participant,
            unrecognized=submission.unrecognized,
            total_problems=len(problem_set),
        )
        for problem_id in sorted(submission.answers):
            graded = grade_answer(
                problem_set.problems[problem_id],
                submission.answers[problem_id],
                problem_id=problem_id,
                participant=submission.participant,
                verifier=verifier,
                timeout_s=timeout_s,
                run_directory=run_directory,
            )
            participant.grades.append(graded)
            if on_grade is not None:
                on_grade(graded)
        result.participants.append(participant)

    # -- stage 3: everything, refused answers included ----------------
    result.statistics = summarize_contest(
        result.grades(),
        problem_ids=problem_set.ids(),
        participants=len(submissions),
    )

    # Reconciled whether or not a results folder is written: an unverified
    # toolchain is a fact about the run, not about its output.
    observed = observed_toolchain(verifier)
    result.toolchain_warnings = tuple(reconcile_toolchain(problem_set.manifest, observed))
    result.problems_without_provenance = problem_set.without_provenance()

    if run_directory is not None:
        run_directory.write_manifest(
            problems=problem_set.problems,
            participants=[s.participant for s in submissions],
            backend=getattr(verifier, "name", None),
            problem_metadata=problem_set.metadata,
            benchmark=problem_set.manifest,
            observed_toolchain=observed,
            toolchain_warnings=reconcile_toolchain(problem_set.manifest, observed),
        )
        run_directory.write_answers(result.grades())
        run_directory.write_report(
            result.statistics,
            leaderboard=[p.to_dict() for p in result.leaderboard()],
            graded=result.grades(),
        )
        result.run_directory = run_directory.root
    return result
