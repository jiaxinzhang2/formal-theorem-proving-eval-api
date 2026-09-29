"""Aggregate every frozen-interface verdict, including refused and unverified answers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

from ...spec.stage import StageId as Stage, StageStatus

if TYPE_CHECKING:
    from .results import GradedAnswer

__all__ = ["ProblemStatistics", "ContestStatistics", "summarize_contest"]


@dataclass
class ProblemStatistics:
    """One problem, across all participants."""

    problem_id: str
    attempted: int = 0
    solved: int = 0
    refused_at_interface: int = 0
    refused_at_kernel: int = 0
    refused_at_replay: int = 0
    refused_at_dependencies: int = 0
    refused_at_axioms: int = 0
    not_compiled: int = 0

    @property
    def solve_rate(self) -> float:
        return self.solved / self.attempted if self.attempted else 0.0

    @property
    def unsolved(self) -> bool:
        return self.solved == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "attempted": self.attempted,
            "solved": self.solved,
            "solve_rate": round(self.solve_rate, 4),
            "refused_at_interface": self.refused_at_interface,
            "refused_at_kernel": self.refused_at_kernel,
            "refused_at_replay": self.refused_at_replay,
            "refused_at_dependencies": self.refused_at_dependencies,
            "refused_at_axioms": self.refused_at_axioms,
            "not_compiled": self.not_compiled,
        }


@dataclass
class ContestStatistics:
    """The whole run, aggregated."""

    problems: int = 0
    participants: int = 0
    answers: int = 0
    solved: int = 0
    kernel_checked: int = 0
    #: Answers refused at each stage.
    refused_at: dict[str, int] = field(default_factory=dict)
    #: Why, at whatever granularity the stage reported.
    reasons: dict[str, int] = field(default_factory=dict)
    #: Reward-hacking tricks seen, by pattern id.
    hack_patterns: dict[str, int] = field(default_factory=dict)
    by_problem: list[ProblemStatistics] = field(default_factory=list)
    total_compile_time_s: float = 0.0

    @property
    def solve_rate(self) -> float:
        return self.solved / self.answers if self.answers else 0.0

    @property
    def unsolved_problems(self) -> tuple[str, ...]:
        return tuple(p.problem_id for p in self.by_problem if p.unsolved)

    @property
    def unattempted_problems(self) -> tuple[str, ...]:
        return tuple(p.problem_id for p in self.by_problem if p.attempted == 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problems": self.problems,
            "participants": self.participants,
            "answers": self.answers,
            "solved": self.solved,
            "solve_rate": round(self.solve_rate, 4),
            "kernel_checked": self.kernel_checked,
            "refused_at": self.refused_at,
            "reasons": self.reasons,
            "hack_patterns": self.hack_patterns,
            "unsolved_problems": list(self.unsolved_problems),
            "unattempted_problems": list(self.unattempted_problems),
            "total_compile_time_s": round(self.total_compile_time_s, 2),
            "by_problem": [p.to_dict() for p in self.by_problem],
        }

    def format_text(self) -> str:
        lines = [
            "answers graded: %d  solved: %d (%.1f%%)  kernel-checked: %d"
            % (self.answers, self.solved, 100 * self.solve_rate, self.kernel_checked),
        ]
        if self.kernel_checked < self.answers:
            lines.append(
                "  %d answer(s) have no conclusive kernel verdict; none count as solved."
                % (self.answers - self.kernel_checked)
            )
        if self.refused_at:
            lines.append(
                "refused at: "
                + ", ".join(
                    "%s=%d" % kv for kv in sorted(self.refused_at.items(), key=lambda kv: -kv[1])
                )
            )
        if self.reasons:
            lines.append("top reasons:")
            for reason, count in sorted(self.reasons.items(), key=lambda kv: -kv[1])[:8]:
                lines.append("  %4d  %s" % (count, reason[:88]))
        if self.hack_patterns:
            lines.append(
                "reward hacking seen: "
                + ", ".join(
                    "%s=%d" % kv
                    for kv in sorted(self.hack_patterns.items(), key=lambda kv: -kv[1])
                )
            )
        if self.unsolved_problems:
            shown = ", ".join(self.unsolved_problems[:12])
            lines.append(
                "solved by nobody (%d): %s%s"
                % (
                    len(self.unsolved_problems),
                    shown,
                    " ..." if len(self.unsolved_problems) > 12 else "",
                )
            )
        if self.total_compile_time_s:
            lines.append("prover time: %.1fs total" % self.total_compile_time_s)
        return "\n".join(lines)


def summarize_contest(
    graded: Sequence[GradedAnswer],
    *,
    problem_ids: Sequence[str] = (),
    participants: int = 0,
) -> ContestStatistics:
    """Aggregate every graded answer, refused ones included."""
    stats = ContestStatistics(
        problems=len(problem_ids) or len({g.problem_id for g in graded}),
        participants=participants or len({g.participant for g in graded if g.participant}),
        answers=len(graded),
    )
    refused: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    hacks: Counter[str] = Counter()
    per_problem: dict[str, ProblemStatistics] = {
        pid: ProblemStatistics(problem_id=pid) for pid in problem_ids
    }

    for answer in graded:
        problem = per_problem.setdefault(
            answer.problem_id, ProblemStatistics(problem_id=answer.problem_id)
        )
        problem.attempted += 1
        if answer.solved:
            stats.solved += 1
            problem.solved += 1
        if answer.kernel_checked:
            stats.kernel_checked += 1
        elif answer.compile_status is StageStatus.NOT_RUN:
            problem.not_compiled += 1
        if answer.compile_time_s:
            stats.total_compile_time_s += answer.compile_time_s

        failed_at = answer.failed_at
        if failed_at is not None:
            refused[failed_at.value] += 1
            reasons[answer.reason.split("(")[0].strip()[:90]] += 1
            if failed_at is Stage.INTERFACE:
                problem.refused_at_interface += 1
            elif failed_at is Stage.KERNEL:
                problem.refused_at_kernel += 1
            elif failed_at is Stage.REPLAY:
                problem.refused_at_replay += 1
            elif failed_at is Stage.DEPENDENCIES:
                problem.refused_at_dependencies += 1
            elif failed_at is Stage.AXIOMS:
                problem.refused_at_axioms += 1

        from ...backends.soundness import parse_label

        violations: list[str] = []
        if answer.report:
            violations.extend(detail for fault, detail in answer.report.faults if fault.value == "reward_hacking")
        if answer.axiom_audit:
            violations.extend(answer.axiom_audit.violations)
        for detail in violations:
            for violation in detail.split("; "):
                _, pattern_id = parse_label(violation)
                if pattern_id:
                    hacks[pattern_id] += 1

    stats.refused_at = dict(refused)
    stats.reasons = dict(reasons)
    stats.hack_patterns = dict(hacks)
    stats.by_problem = [per_problem[pid] for pid in sorted(per_problem)]
    return stats
