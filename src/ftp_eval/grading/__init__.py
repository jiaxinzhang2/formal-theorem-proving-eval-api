"""Grading answers in three stages.

The contest pipeline, made explicit. Every answer goes through the same
three stages in order, and each one answers a different question::

    answer.lean
        │
        │  ① MATCH      does it state the theorem that was set?
        │               ftp_eval.formalizing.matching
        │               text only, no prover, milliseconds
        ▼
    the right theorem, with a proof attached
        │
        │  ② COMPILE    does the prover accept that proof --
        │               of the THEOREM's proposition, not the answer's?
        │               ftp_eval.grading.compiling -> ftp_eval.proving
        │               one Lean compile per answer
        ▼
    a verdict per answer
        │
        │  ③ REPORT     what happened, across every answer,
        │               correct and incorrect alike
        │               ftp_eval.grading.statistics -> ftp_eval.analysis
        ▼
    scores, failure modes, per-problem difficulty

**The stages run in order and stop early.** Compiling an answer that does
not state the theorem would be wasted work and, worse, a compile that
*succeeds* on the wrong theorem reads like a pass. So stage ② only ever
sees answers that cleared stage ①, and :class:`GradedAnswer.stage_reached`
records how far each one got -- there is never a result whose provenance is
unclear.

**Stage ③ takes everything.** Answers that failed at stage ① are as much a
part of the statistics as the ones that passed: "12 answers restated the
theorem" and "30 failed to compile" call for completely different
responses from a setter, and a pipeline that discarded the early failures
could not tell you either.

Entry points:

* :func:`~ftp_eval.grading.contest.grade_contest` -- N problems, many
  participants, the whole pipeline.
* :class:`~ftp_eval.grading.stages.GradedAnswer` -- one answer's trip
  through all three.
"""

from __future__ import annotations

from .compiling import CompileOutcome, compile_answer
from .contest import (
    AnswerGrade,
    ContestResult,
    ParticipantResult,
    ProblemSet,
    Submission,
    grade_contest,
    load_problem_set,
    load_submissions,
)
from .stages import GradedAnswer, Stage, StageStatus
from .statistics import ContestStatistics, summarize_contest

__all__ = [
    # the stages
    "Stage",
    "StageStatus",
    "GradedAnswer",
    # stage 2
    "compile_answer",
    "CompileOutcome",
    # stage 3
    "summarize_contest",
    "ContestStatistics",
    # the driver
    "grade_contest",
    "load_problem_set",
    "load_submissions",
    "ProblemSet",
    "Submission",
    "AnswerGrade",
    "ParticipantResult",
    "ContestResult",
]
