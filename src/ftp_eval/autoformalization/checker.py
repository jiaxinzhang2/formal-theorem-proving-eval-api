"""Checking a formalization against the problem it came from.

This is the other half of the API. Given three artifacts -- a problem in
natural language, a formal statement, a formal proof -- there are two
links to check, and they fail in opposite directions:

* A **wrong formalization with a valid proof** is a false positive. The
  prover says yes and the number goes up, but nothing was proved about
  the actual problem. This is the expensive failure, and it is invisible
  to any amount of proof checking.
* A **right formalization with no proof** is an honest miss.

``StatementChecker`` handles the first, and it asks exactly one question::

    judge_faithful    does it mean what the prose says?      an LLM

That is the whole API. **There is no prover here, and no text analysis
either.** "Does this Lean say what that English says" is not a question a
prover can be asked, and every question that *can* be asked of a prover or
of a regex is a question about the Lean rather than about the problem.

That is the real asymmetry with ``proving/``. There the kernel is ground
truth and no model is involved. Here **there is no ground truth**, so the
affirmative answer comes from a judge that can be wrong. Everything in
``judge.py`` -- abstention as a real answer, rechecking rejections, keeping
every sample's label, never turning an outage into a rejection -- exists
because of that one fact.

Five checks used to live here: does the statement elaborate, is it
trivially closable, are its hypotheses contradictory, is it equivalent to a
reference, does it declare an axiom. Not one of them looks at the prose, so
none of them was ever answering this API's question. They are in
:mod:`ftp_eval.proving.grading.problem_health` now, answering the one they
were really asking -- whether a problem set can be graded fairly at all.

Run both over a problem folder before publishing it. ``ftp-eval audit``
does, and reports them separately, because "this problem is ungradeable"
and "this problem does not match its prose" are different failures with
different fixes.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from ..backends.types import StatementTask
from .judge import Judge, JudgeError
from .types import Check, CheckKind, JudgeLabel, StatementStatus, StatementVerdict

__all__ = ["StatementChecker"]


class StatementChecker:
    """Assess whether a formal statement says what its problem says.

    Parameters
    ----------
    judge:
        Used for semantic faithfulness, which is the only thing here that
        can return a positive verdict. Pass ``None`` to run the text screen
        alone -- the result is then at best ``INCONCLUSIVE``, and
        ``checked_faithfulness`` records that no judge was consulted rather
        than implying a clean bill of health.
    """

    def __init__(self, judge: Judge | None = None, *, timeout_s: float = 120.0) -> None:
        self.judge = judge
        self.timeout_s = timeout_s

    # -- the method callers use ---------------------------------------

    def check(self, task: StatementTask) -> StatementVerdict:
        """Run every configured check. Never raises."""
        started = time.monotonic()
        checks: list[Check] = []
        raw: dict[str, Any] = {}

        judge_verdict = None
        if self.judge is not None and not task.informal_statement.strip():
            # No prose means there is nothing to judge faithfulness against,
            # so calling the judge would spend money to learn nothing. Over a
            # 100-problem set with a paid judge that is 100 wasted calls.
            checks.append(
                Check(
                    CheckKind.JUDGE_FAITHFUL,
                    None,
                    "the problem records no prose, so there is nothing to judge the "
                    "formalization against",
                )
            )
        elif self.judge is not None:
            try:
                judge_verdict = self.judge.judge(task)
            except JudgeError as exc:
                checks.append(
                    Check(CheckKind.JUDGE_FAITHFUL, None, "judge failed: %s" % exc)
                )
            else:
                checks.append(self._check_from_judge(judge_verdict))
        else:
            checks.append(
                Check(
                    CheckKind.JUDGE_FAITHFUL,
                    None,
                    "no judge configured; semantic faithfulness was not assessed",
                )
            )

        return StatementVerdict(
            task_id=task.task_id,
            status=self._status(checks),
            checks=tuple(checks),
            judge=judge_verdict,
            backend=None,
            wall_time_s=time.monotonic() - started,
            split=task.split,
            raw=raw,
        )

    def check_many(self, tasks: Sequence[StatementTask]) -> list[StatementVerdict]:
        return [self.check(task) for task in tasks]

    # -- individual checks --------------------------------------------

    def _check_from_judge(self, verdict: Any) -> Check:
        label = verdict.label
        if label is JudgeLabel.FAITHFUL:
            return Check(CheckKind.JUDGE_FAITHFUL, True, verdict.reasoning[:400])
        if label is JudgeLabel.UNFAITHFUL:
            return Check(CheckKind.JUDGE_FAITHFUL, False, verdict.reasoning[:400])
        # An abstention is not a failure, and must not be counted as one.
        return Check(
            CheckKind.JUDGE_FAITHFUL,
            None,
            "judge abstained (%s): %s" % (verdict.aggregation, verdict.reasoning[:300]),
        )

    def _status(self, checks: Sequence[Check]) -> StatementStatus:
        """Combine checks into one verdict.

        Three outcomes, and the third is the important one: a judgement
        that was never obtained is ``INCONCLUSIVE``, never a pass. No
        judge, no prose to judge against, an abstention and an API outage
        all land there, because none of them is evidence that the
        formalization is right.
        """
        if any(c.passed is False for c in checks):
            return StatementStatus.UNFAITHFUL
        if any(c.passed is True for c in checks):
            return StatementStatus.OK
        return StatementStatus.INCONCLUSIVE


def summarize_statements(verdicts: Sequence[StatementVerdict]) -> dict[str, Any]:
    """Roll statement verdicts up for a report."""
    from collections import Counter

    from .judge import aggregate_usage

    statuses: Counter[str] = Counter(v.status.value for v in verdicts)
    failed_checks: Counter[str] = Counter()
    for v in verdicts:
        for c in v.failures:
            failed_checks[c.kind.value] += 1
    usage = aggregate_usage([v.judge for v in verdicts])
    checked = sum(1 for v in verdicts if v.checked_faithfulness)
    return {
        "statements": len(verdicts),
        "statuses": dict(statuses),
        "failed_checks": dict(failed_checks),
        "faithfulness_assessed": checked,
        "faithfulness_unassessed": len(verdicts) - checked,
        "judge_usage": usage.to_dict(),
    }


def format_statement_summary(verdicts: Sequence[StatementVerdict]) -> str:
    """Human-readable statement report."""
    data = summarize_statements(verdicts)
    lines = ["statements checked: %d" % data["statements"]]
    for status, count in sorted(data["statuses"].items(), key=lambda kv: -kv[1]):
        lines.append("  %-13s %d" % (status, count))
    if data["failed_checks"]:
        lines.append(
            "failed checks: "
            + ", ".join("%s=%d" % kv for kv in sorted(data["failed_checks"].items()))
        )
    if data["faithfulness_unassessed"]:
        lines.append(
            "NOTE: %d statement(s) had no faithfulness assessment (no judge, or the judge "
            "abstained). Structural checks alone cannot tell you a formalization means "
            "the right thing." % data["faithfulness_unassessed"]
        )
    usage = data["judge_usage"]
    if usage["calls"]:
        cost = usage["estimated_cost_usd"]
        lines.append(
            "judge: %d call(s), %d in / %d out tokens, %d read from cache; cost %s"
            % (
                usage["calls"],
                usage["input_tokens"],
                usage["output_tokens"],
                usage["cache_read_input_tokens"],
                "unknown" if cost is None else "~$%.4f (ESTIMATE, verify against billing)" % cost,
            )
        )
    return "\n".join(lines)
