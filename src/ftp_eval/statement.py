"""Checking a formalization against the problem it came from.

This is the other half of the API. Given three artifacts -- a problem in
natural language, a formal statement, a formal proof -- there are two
links to check, and they fail in opposite directions:

* A **wrong formalization with a valid proof** is a false positive. The
  prover says yes and the number goes up, but nothing was proved about
  the actual problem. This is the expensive failure, and it is invisible
  to any amount of proof checking.
* A **right formalization with no proof** is an honest miss.

``StatementChecker`` handles the first. It combines what the prover can
decide (does it elaborate, is it trivially closable, are the hypotheses
contradictory, does it match a reference) with what only a judge can
assess (does it mean the same thing as the prose), and keeps the two kinds
of evidence distinct in the result.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from .judge import Judge, JudgeError
from .soundness import screen_source
from .types import (
    Check,
    CheckKind,
    JudgeLabel,
    ProbeKind,
    StatementStatus,
    StatementTask,
    StatementVerdict,
    Status,
)
from .verifier import Verifier

__all__ = ["StatementChecker"]


class StatementChecker:
    """Assess whether a formal statement says what its problem says.

    Parameters
    ----------
    verifier:
        Used for the prover-decidable probes. Pass ``None`` to run judge
        checks only; the prover checks then report "did not run" rather
        than passing by default.
    judge:
        Used for semantic faithfulness. Pass ``None`` to skip it -- the
        verdict is then at best ``INCONCLUSIVE`` on faithfulness, which
        the result records honestly via ``checked_faithfulness``.
    """

    def __init__(
        self,
        verifier: Verifier | None = None,
        judge: Judge | None = None,
        *,
        timeout_s: float = 120.0,
        check_trivial: bool = True,
        check_vacuous: bool = True,
        check_gold: bool = True,
    ) -> None:
        self.verifier = verifier
        self.judge = judge
        self.timeout_s = timeout_s
        self.check_trivial = check_trivial
        self.check_vacuous = check_vacuous
        self.check_gold = check_gold

    # -- the method callers use ---------------------------------------

    def check(self, task: StatementTask) -> StatementVerdict:
        """Run every configured check. Never raises."""
        started = time.monotonic()
        checks: list[Check] = []
        raw: dict[str, Any] = {}

        checks.append(self._check_no_placeholder(task))
        if self.verifier is not None:
            checks.extend(self._prover_checks(task, raw))
        else:
            for kind in (CheckKind.ELABORATES, CheckKind.NON_TRIVIAL, CheckKind.NON_VACUOUS):
                checks.append(
                    Check(kind, None, "no verifier configured, so this was not checked")
                )

        judge_verdict = None
        if self.judge is not None:
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
            backend=self.verifier.name if self.verifier else None,
            wall_time_s=time.monotonic() - started,
            split=task.split,
            raw=raw,
        )

    def check_many(self, tasks: Sequence[StatementTask]) -> list[StatementVerdict]:
        return [self.check(task) for task in tasks]

    # -- individual checks --------------------------------------------

    def _check_no_placeholder(self, task: StatementTask) -> Check:
        """The statement itself must not contain a hole or an axiom.

        A formalization carrying ``sorry`` or declaring an axiom is broken
        before any proof is attempted, and running the prover on it would
        report a confusing compile error instead of the real problem.
        """
        report = screen_source(
            task.formal_statement,
            task.language,
            required_statement=None,
            allowed_imports=None,
        )
        if report.ok:
            return Check(CheckKind.NO_PLACEHOLDER, True, "statement is free of holes and axioms")
        return Check(
            CheckKind.NO_PLACEHOLDER,
            False,
            "; ".join(report.violations),
            fatal=True,
        )

    def _prover_checks(self, task: StatementTask, raw: dict[str, Any]) -> list[Check]:
        assert self.verifier is not None
        checks: list[Check] = []

        elaborates = self._run_probe(task, ProbeKind.ELABORATES, raw)
        if elaborates is None:
            checks.append(
                Check(
                    CheckKind.ELABORATES,
                    None,
                    "backend %r cannot build an elaboration probe for %s"
                    % (self.verifier.name, task.language),
                )
            )
        elif elaborates is Status.VERIFIED:
            checks.append(Check(CheckKind.ELABORATES, True, "statement typechecks on its own"))
        elif elaborates in (Status.ERROR, Status.SKIPPED):
            checks.append(
                Check(CheckKind.ELABORATES, None, "probe could not run (%s)" % elaborates.value)
            )
        else:
            # This is decisive: a statement that does not typecheck cannot
            # be the formalization of anything.
            checks.append(
                Check(
                    CheckKind.ELABORATES,
                    False,
                    "statement does not typecheck, so it is not a well-formed "
                    "formalization (%s)" % elaborates.value,
                    fatal=True,
                )
            )
            # The remaining probes would all fail for the same reason.
            for kind in (CheckKind.NON_TRIVIAL, CheckKind.NON_VACUOUS):
                checks.append(Check(kind, None, "skipped: the statement does not typecheck"))
            return checks

        if self.check_trivial:
            checks.append(self._check_trivial(task, raw))
        if self.check_vacuous:
            checks.append(self._check_vacuous(task, raw))
        if self.check_gold and task.gold_formal_statement:
            checks.append(self._check_gold(task, raw))
        return checks

    def _check_trivial(self, task: StatementTask, raw: dict[str, Any]) -> Check:
        """A goal a one-liner closes is usually a lost formalization.

        Not always: some problems really are one ``simp`` away, so this is
        reported as suspicious rather than fatal. What it reliably catches
        is the degenerate case -- a statement collapsed to ``True``, or to
        a closed arithmetic identity ``decide`` evaluates.
        """
        outcome = self._run_probe(task, ProbeKind.TRIVIAL, raw)
        if outcome is None:
            return Check(CheckKind.NON_TRIVIAL, None, "no triviality probe available")
        if outcome in (Status.ERROR, Status.SKIPPED):
            return Check(CheckKind.NON_TRIVIAL, None, "probe could not run (%s)" % outcome.value)
        if outcome is Status.VERIFIED:
            return Check(
                CheckKind.NON_TRIVIAL,
                False,
                "a single cheap tactic closes this goal, which usually means the "
                "formalization lost the content of the problem",
            )
        return Check(CheckKind.NON_TRIVIAL, True, "not closable by a cheap tactic")

    def _check_vacuous(self, task: StatementTask, raw: dict[str, Any]) -> Check:
        """Contradictory hypotheses make a theorem true and worthless.

        This is the reward hack the kernel cannot help with at all: the
        proof is *genuinely valid*, the kernel is *entirely satisfied*,
        and the theorem still says nothing, because anything follows from
        a contradiction. It can only be caught by interrogating the
        statement, which is why it lives here and not in the proof checks.
        """
        outcome = self._run_probe(task, ProbeKind.VACUOUS, raw)
        if outcome is None:
            return Check(
                CheckKind.NON_VACUOUS,
                None,
                "no vacuity probe available (the statement has no hypotheses, or does "
                "not parse)",
            )
        if outcome in (Status.ERROR, Status.SKIPPED):
            return Check(CheckKind.NON_VACUOUS, None, "probe could not run (%s)" % outcome.value)
        if outcome is Status.VERIFIED:
            return Check(
                CheckKind.NON_VACUOUS,
                False,
                "`False` is derivable from the hypotheses, so the statement is "
                "vacuously true and any proof of it proves nothing",
            )
        return Check(CheckKind.NON_VACUOUS, True, "hypotheses are not contradictory")

    def _check_gold(self, task: StatementTask, raw: dict[str, Any]) -> Check:
        """Compare against a reference formalization, via the prover.

        Asking the prover for an ``Iff`` is far stronger than comparing
        text: it accepts a differently-phrased but equivalent statement,
        which textual comparison would wrongly reject.
        """
        outcome = self._run_probe(task, ProbeKind.GOLD_EQUIVALENT, raw)
        if outcome is None:
            return Check(
                CheckKind.GOLD_EQUIVALENT,
                None,
                "cannot compare to the reference: the two statements have different "
                "binders, so no equivalence can be stated between them",
            )
        if outcome in (Status.ERROR, Status.SKIPPED):
            return Check(
                CheckKind.GOLD_EQUIVALENT, None, "probe could not run (%s)" % outcome.value
            )
        if outcome is Status.VERIFIED:
            return Check(
                CheckKind.GOLD_EQUIVALENT, True, "provably equivalent to the reference statement"
            )
        # Failing to *prove* the equivalence is not proof of inequivalence:
        # the tactics tried are weak, and a true equivalence can be hard.
        return Check(
            CheckKind.GOLD_EQUIVALENT,
            None,
            "equivalence with the reference could not be proved by the cheap tactics "
            "tried; this is not evidence that the two differ",
        )

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

    # -- plumbing -----------------------------------------------------

    def _run_probe(
        self, task: StatementTask, kind: ProbeKind, raw: dict[str, Any]
    ) -> Status | None:
        assert self.verifier is not None
        source = self.verifier.build_probe(task, kind)
        if source is None:
            return None
        result = self.verifier.probe(task, source, timeout_s=self.timeout_s)
        raw["probe_%s" % kind.value] = {
            "status": result.status.value,
            "source": source,
            "diagnostics": [d.to_dict() for d in result.diagnostics[:3]],
        }
        return result.status

    def _status(self, checks: Sequence[Check]) -> StatementStatus:
        """Combine checks into one verdict.

        Ordering: anything fatal makes it ``MALFORMED``; any other failed
        check makes it ``SUSPICIOUS``; all-passed with at least one
        faithfulness check makes it ``OK``; otherwise ``INCONCLUSIVE``,
        because passing only the structural checks is not evidence that
        the statement means the right thing.
        """
        if any(c.passed is False and c.fatal for c in checks):
            return StatementStatus.MALFORMED
        if any(c.passed is False for c in checks):
            return StatementStatus.SUSPICIOUS
        assessed_meaning = any(
            c.kind in (CheckKind.JUDGE_FAITHFUL, CheckKind.GOLD_EQUIVALENT) and c.passed is True
            for c in checks
        )
        if assessed_meaning:
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
