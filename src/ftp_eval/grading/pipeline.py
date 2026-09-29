"""The three stages as :class:`~ftp_eval.spec.stage.GradingStage` objects.

This is where the contract in :mod:`ftp_eval.spec` becomes real. Each stage
is a class with the same shape, so running them is a loop::

    for stage in STAGES:
        ok, why = stage.should_run(context)
        if not ok:
            record NOT_RUN with `why`
            break
        result = stage.run(context)
        record it
        if not result.passed:
            break

Nothing about the ordering lives in that loop -- each stage declares what
must have passed before it -- so adding a stage is adding a class, not
editing a driver.
"""

from __future__ import annotations

import time
from typing import Sequence

from ..formalizing.matching import match_submission
from ..spec.stage import GradingStage, StageContext, StageId, StageResult, StageStatus
from .compiling import compile_answer

__all__ = ["MatchStage", "CompileStage", "STAGES", "run_stages"]


class MatchStage(GradingStage):
    """Stage ①: does the answer state the theorem that was set?

    Text only. Catches a different theorem, a weakened hypothesis, a gutted
    definition, an unproved helper the proof cites, a changed namespace, and
    reward hacking -- none of which needs a prover, and all of which would
    otherwise be invisible to a compile that happens to succeed.
    """

    id = StageId.MATCH
    requires = None

    def run(self, context: StageContext) -> StageResult:
        started = time.monotonic()
        try:
            report = match_submission(
                context.problem_source, context.answer_source, target=context.target
            )
        except Exception as exc:  # pragma: no cover - defensive
            # One unparseable answer must not be able to lose a contest.
            return StageResult(
                self.id,
                StageStatus.NOT_RUN,
                "matching raised %s: %s" % (type(exc).__name__, exc),
                elapsed_s=time.monotonic() - started,
            )
        return StageResult(
            self.id,
            StageStatus.PASSED if report.status.answers_the_problem else StageStatus.FAILED,
            _match_detail(report),
            payload=report,
            elapsed_s=time.monotonic() - started,
        )


def _match_detail(report) -> str:
    fatal = report.fatal_mismatches
    if fatal:
        return "%s: %s" % (fatal[0].kind.value, fatal[0].detail)
    if report.status.answers_the_problem:
        return "states the theorem and supplies a proof"
    return report.status.value


class CompileStage(GradingStage):
    """Stage ②: does the prover accept the proof?

    Not "does the answer file compile". What is compiled is the *theorem's*
    proposition closed with the *answer's* proof term, over the *theorem's*
    definitions -- see :mod:`ftp_eval.grading.compiling`.
    """

    id = StageId.COMPILE
    requires = StageId.MATCH

    def available(self, context: StageContext) -> tuple[bool, str]:
        if context.verifier is None:
            return False, "no prover configured"
        try:
            info = context.verifier.info()
        except Exception as exc:  # pragma: no cover - defensive
            return False, "backend info failed: %s" % exc
        if not info.available:
            return False, "backend %r unavailable: %s" % (info.name, info.detail)
        return True, ""

    def run(self, context: StageContext) -> StageResult:
        started = time.monotonic()
        outcome = compile_answer(
            context.verifier,
            context.problem_source,
            context.answer_source,
            target=context.target,
            timeout_s=context.timeout_s,
        )
        return StageResult(
            self.id,
            outcome.status,
            outcome.detail,
            payload=outcome,
            elapsed_s=outcome.compile_time_s or (time.monotonic() - started),
        )


#: The stages, in execution order. Stage ③ is not here: it runs once over
#: the whole set rather than per answer, which is what makes it able to
#: report on the answers the earlier stages refused.
STAGES: tuple[GradingStage, ...] = (MatchStage(), CompileStage())


def run_stages(
    context: StageContext, stages: Sequence[GradingStage] = STAGES
) -> dict[StageId, StageResult]:
    """Run the per-answer stages in order, stopping at the first failure.

    Returns every stage's conclusion, including a ``NOT_RUN`` entry for any
    stage that was skipped and why -- so there is no result whose provenance
    is unclear.
    """
    results: dict[StageId, StageResult] = {}
    for stage in stages:
        active = StageContext(
            problem_id=context.problem_id,
            problem_source=context.problem_source,
            answer_source=context.answer_source,
            participant=context.participant,
            target=context.target,
            verifier=context.verifier,
            timeout_s=context.timeout_s,
            prior=dict(results),
        )
        ok, why = stage.should_run(active)
        if not ok:
            results[stage.id] = StageResult(stage.id, StageStatus.NOT_RUN, why)
            break
        result = stage.run(active)
        results[stage.id] = result
        if not result.passed:
            break
    return results
