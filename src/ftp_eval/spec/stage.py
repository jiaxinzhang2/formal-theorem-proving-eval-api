"""What a grading stage is.

All three stages have the same shape, which is what makes the pipeline a
loop rather than three special cases. A stage:

* declares its number and name, so results are self-describing;
* declares what must have passed before it (``requires``), so the ordering
  is data rather than control flow buried in a driver;
* says whether it can run at all (``available``), separately from whether
  the answer passed it;
* returns a :class:`StageResult` and never raises.

That last point is the contract that matters most. A grader that throws on
one answer loses the run; a grader that swallows the error silently scores
the answer wrong. So every stage catches its own failures and reports them
as ``NOT_RUN`` with a reason.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

__all__ = ["StageId", "StageStatus", "StageContext", "StageResult", "GradingStage"]


class StageId(str, Enum):
    """The three stages, numbered in execution order."""

    MATCH = "match"
    COMPILE = "compile"
    REPORT = "report"

    @property
    def number(self) -> int:
        return {"match": 1, "compile": 2, "report": 3}[self.value]


class StageStatus(str, Enum):
    """How an answer fared at a stage.

    ``NOT_RUN`` is a first-class outcome and is never collapsed into either
    other value. "No prover was configured" and "the prover rejected it"
    are different facts, and conflating them would report unverified
    answers as correct.
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"

    @property
    def conclusive(self) -> bool:
        return self is not StageStatus.NOT_RUN


@dataclass(frozen=True)
class StageContext:
    """Everything a stage is given about one answer."""

    problem_id: str
    problem_source: str
    answer_source: str
    participant: str = ""
    #: Fully-qualified theorem name, when the benchmark pins it.
    target: str | None = None
    #: The prover, when one is configured. ``None`` means stage 2 cannot run.
    verifier: Any = None
    timeout_s: float = 300.0
    #: Conclusions from stages that already ran, keyed by :class:`StageId`.
    prior: Mapping[StageId, "StageResult"] = field(default_factory=dict)

    def passed(self, stage: StageId) -> bool:
        result = self.prior.get(stage)
        return result is not None and result.status is StageStatus.PASSED


@dataclass
class StageResult:
    """What a stage concluded about one answer."""

    stage: StageId
    status: StageStatus
    #: One line a human can act on. Required: a bare status is not a reason.
    detail: str = ""
    #: The stage's own structured output -- a ``MatchReport``, a
    #: ``CompileOutcome``. Typed loosely so the contract does not depend on
    #: any one stage's types.
    payload: Any = None
    elapsed_s: float | None = None

    @property
    def passed(self) -> bool:
        return self.status is StageStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload
        return {
            "stage": self.stage.value,
            "stage_number": self.stage.number,
            "status": self.status.value,
            "detail": self.detail,
            "elapsed_s": None if self.elapsed_s is None else round(self.elapsed_s, 4),
            "payload": payload.to_dict() if hasattr(payload, "to_dict") else payload,
        }


class GradingStage(abc.ABC):
    """One stage of the pipeline."""

    #: Which stage this is.
    id: StageId
    #: The stage that must have PASSED before this one runs. ``None`` for
    #: the first. Ordering is therefore declared, not buried in a driver.
    requires: StageId | None = None

    @abc.abstractmethod
    def run(self, context: StageContext) -> StageResult:
        """Grade one answer at this stage.

        **Must not raise.** A prover that crashes, a file that will not
        parse, a timeout: all of these are ``StageStatus.NOT_RUN`` with a
        reason. One bad answer must not be able to lose a whole contest.
        """

    def available(self, context: StageContext) -> tuple[bool, str]:
        """Whether this stage can run at all, and why not if it cannot.

        Distinct from whether an answer passes: a missing prover makes
        stage 2 unavailable for every answer, which the report states
        rather than letting ``solved`` quietly mean something weaker.
        """
        return True, ""

    def should_run(self, context: StageContext) -> tuple[bool, str]:
        """Whether to run, given what earlier stages concluded."""
        if self.requires is not None and not context.passed(self.requires):
            return False, "stage %d (%s) did not pass" % (
                self.requires.number,
                self.requires.value,
            )
        return self.available(context)

    @property
    def number(self) -> int:
        return self.id.number

    def __repr__(self) -> str:
        return "<stage %d %s>" % (self.number, self.id.value)
