"""Benchmark report records: checking verdicts with metrics, participants and runs.

GradedAnswer inherits the checker's solved rule unchanged. Participant and run
records aggregate those verdicts without rechecking acceptance.
"""
from __future__ import annotations

from pathlib import Path
from .summary import ContestStatistics
from dataclasses import dataclass, field
from typing import Any, Mapping

from ...backends.types import Status
from ...spec.stage import StageId, StageStatus
from ..checking.interface import InterfaceVerdict

Stage = StageId
__all__ = ["Stage", "StageStatus", "GradedAnswer", "AnswerGrade", "ParticipantResult", "ContestResult"]

@dataclass
class GradedAnswer(InterfaceVerdict):
    tactics: tuple[str, ...] = ()
    structure: Mapping[str, Any] = field(default_factory=dict)
    failure_mode: str = ""
    success_mode: str = ""
    modules: Mapping[str, str] = field(default_factory=dict)

    @property
    def compile_time_s(self) -> float | None:
        return self.build.compile_time_s if self.build else None

    @property
    def compile_status(self) -> StageStatus:
        for event in reversed(self.stages):
            if event["stage"] == "kernel" and event["status"] != "running":
                return StageStatus(event["status"])
        if self.build is None or self.build.status in (Status.ERROR, Status.SKIPPED):
            return StageStatus.NOT_RUN
        return StageStatus.PASSED if self.build.verified else StageStatus.FAILED

    @property
    def failed_at(self) -> Stage | None:
        if self.compile_status is StageStatus.NOT_RUN and self.refused_at == "kernel":
            return None
        return Stage(self.refused_at) if self.refused_at else None

    @property
    def stage_reached(self) -> Stage:
        reached = [Stage(e["stage"]) for e in self.stages if e["status"] != "not_run"]
        if reached:
            return max(reached, key=lambda s: s.number)
        if self.axiom_audit is not None:
            return Stage.AXIOMS
        if self.build is not None:
            return Stage.KERNEL
        return Stage.INTERFACE

    @property
    def reason(self) -> str:
        if self.report is not None and self.report.faults:
            return self.report.faults[0][1]
        if self.build is None:
            return "kernel check not run; no supported module-build backend"
        if not self.build.verified:
            return next((d.message for d in self.build.diagnostics if d.message), self.build.status.value)
        if self.axiom_audit is None:
            return "axiom audit not run"
        if not self.axiom_audit.ok:
            return "; ".join(self.axiom_audit.violations)
        return "kernel checked the frozen target; axiom audit passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "stage_reached": self.stage_reached.value,
            "failed_at": self.failed_at.value if self.failed_at else None,
            "reason": self.reason,
            "metrics": {
                "tactics": list(self.tactics),
                "failure_mode": self.failure_mode or None,
                "success_mode": self.success_mode or None,
                "structure": dict(self.structure),
            },
        }


#: Kept as an alias: one graded answer is exactly a ``GradedAnswer``.
AnswerGrade = GradedAnswer


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

        Placeholder-only refusals are excluded. Separate interface or policy
        violations still require review, including those in incomplete answers.
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
