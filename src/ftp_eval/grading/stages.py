"""The three stages, and one answer's trip through them.

Kept in its own module because the pipeline's shape is the thing worth
being able to read: which stage an answer reached, why it stopped, and
what each stage concluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..formalizing.matching import MatchReport, MatchStatus

__all__ = ["Stage", "StageStatus", "GradedAnswer"]


class Stage(str, Enum):
    """Which stage of the pipeline."""

    #: Does the answer state the theorem that was set? Text only.
    MATCH = "match"
    #: Does the prover accept the proof, of the theorem's proposition?
    COMPILE = "compile"
    #: What happened across every answer. Never stops anything.
    REPORT = "report"


class StageStatus(str, Enum):
    """How an answer fared at the stage it reached."""

    #: Cleared the stage; the pipeline continued.
    PASSED = "passed"
    #: Failed here. Later stages did not run.
    FAILED = "failed"
    #: The stage could not run -- no prover configured, probe unbuildable.
    #: Distinct from failing, and must never be read as either verdict.
    NOT_RUN = "not_run"


@dataclass
class GradedAnswer:
    """One answer, through the pipeline.

    ``stage_reached`` and ``stage_status`` together say exactly where the
    answer ended up, so no result is ever of unclear provenance. In
    particular, ``solved`` is only ever true when a stage actually
    established it -- never by default and never by omission.
    """

    problem_id: str
    participant: str = ""
    #: Stage 1.
    match: MatchReport | None = None
    #: Stage 2, when it ran.
    compile_status: StageStatus = StageStatus.NOT_RUN
    compile_detail: str = ""
    compile_time_s: float | None = None
    #: How far the answer got, and how it fared there.
    stage_reached: Stage = Stage.MATCH
    stage_status: StageStatus = StageStatus.NOT_RUN

    @property
    def solved(self) -> bool:
        """Whether the answer is established as proving the theorem.

        Stage ② is the authority when it ran. Without it, stage ① saying
        ``matched`` means "states the right theorem and supplies a proof" --
        as much as can be said without compiling, and the report says so
        rather than implying the proof was checked.
        """
        if self.compile_status is StageStatus.PASSED:
            return True
        if self.compile_status is StageStatus.FAILED:
            return False
        return bool(self.match and self.match.status.answers_the_problem)

    @property
    def kernel_checked(self) -> bool:
        return self.compile_status in (StageStatus.PASSED, StageStatus.FAILED)

    @property
    def failed_at(self) -> Stage | None:
        """The stage that refused the answer, if one did."""
        if self.match and not self.match.status.answers_the_problem:
            return Stage.MATCH
        if self.compile_status is StageStatus.FAILED:
            return Stage.COMPILE
        return None

    @property
    def reason(self) -> str:
        """One line on why the answer ended where it did."""
        if self.failed_at is Stage.MATCH and self.match:
            fatal = self.match.fatal_mismatches
            if fatal:
                return "%s: %s" % (fatal[0].kind.value, fatal[0].detail)
            return self.match.status.value
        if self.failed_at is Stage.COMPILE:
            return self.compile_detail or "the prover rejected the proof"
        if self.compile_status is StageStatus.PASSED:
            return "kernel confirmed the proof of the theorem's proposition"
        if self.match and self.match.status is MatchStatus.MATCHED_BUT_UNPROVED:
            return "states the theorem; no proof supplied"
        if self.match and self.match.status.answers_the_problem:
            return "states the theorem and supplies a proof (not compiled)"
        return "not graded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "participant": self.participant,
            "solved": self.solved,
            "kernel_checked": self.kernel_checked,
            "stage_reached": self.stage_reached.value,
            "stage_status": self.stage_status.value,
            "failed_at": self.failed_at.value if self.failed_at else None,
            "reason": self.reason,
            "compile": {
                "status": self.compile_status.value,
                "detail": self.compile_detail,
                "compile_time_s": self.compile_time_s,
            },
            "match": self.match.to_dict() if self.match else None,
        }

    def format_text(self) -> str:
        mark = "OK  " if self.solved else "NO  "
        return "%s %-14s stage=%-8s %s" % (
            mark,
            self.problem_id[:14],
            (self.failed_at or self.stage_reached).value,
            self.reason[:96],
        )
