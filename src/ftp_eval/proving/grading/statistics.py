"""Stage 3: what happened, across every answer.

Takes **everything**, including the answers that never reached stage 2.
That is the point: "12 answers restated the theorem" and "30 answers
failed to compile" call for completely different responses from a setter,
and a stage that only looked at compiled answers could report neither.

Three cuts, because they answer different questions:

* **By participant** -- the leaderboard.
* **By problem** -- which problems were solved by nobody, by everybody, or
  attempted and refused. The setter's own signal about the problem set.
* **By stage and reason** -- where answers died and why. A spike at stage 1
  usually means the format was unclear, not that the field was weak.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .stages import GradedAnswer, Stage, StageStatus

__all__ = ["ProblemStatistics", "ContestStatistics", "summarize_contest"]


@dataclass
class ProblemStatistics:
    """One problem, across all participants."""

    problem_id: str
    attempted: int = 0
    solved: int = 0
    refused_at_match: int = 0
    refused_at_compile: int = 0
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
            "refused_at_match": self.refused_at_match,
            "refused_at_compile": self.refused_at_compile,
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
                "  %d answer(s) were NOT compiled, so `solved` there means the text "
                "screen passed, not that the proof was checked."
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
            if failed_at is Stage.MATCH:
                problem.refused_at_match += 1
            else:
                problem.refused_at_compile += 1

        if answer.match:
            for mismatch in answer.match.mismatches:
                if mismatch.kind.value == "reward_hacking":
                    from ...shared.soundness import parse_label

                    for violation in mismatch.detail.split("; "):
                        _, pattern_id = parse_label(violation)
                        if pattern_id:
                            hacks[pattern_id] += 1

    stats.refused_at = dict(refused)
    stats.reasons = dict(reasons)
    stats.hack_patterns = dict(hacks)
    stats.by_problem = [per_problem[pid] for pid in sorted(per_problem)]
    return stats
