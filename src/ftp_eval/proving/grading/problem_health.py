"""Is this problem set fit to grade? Not: does it mean the right thing.

These five checks used to live in ``autoformalization/``, and not one of
them looks at the prose -- so none was ever answering that API's question.
Four of them were also the only reason it needed a prover. What they
actually answer is whether a problem can be graded fairly at all, which is
this side's concern.

Each asks something a model cannot reliably answer, because each is really
"does a proof exist?" or "what does this text literally contain?":

    assumes_nothing   does it declare an `axiom`, an `opaque`, or an
                      elaborator setting that weakens the goal?   text only
    elaborates        does the statement typecheck at all?
    non_trivial       can `trivial` / `simp` / `decide` close it outright?
    non_vacuous       is `False` derivable from the hypotheses?
    gold_equivalent   is it provably iff a reference statement?

Only the first needs no prover, so it runs even with no toolchain
installed. The rest are prover questions.

``non_vacuous`` is the one that earns the prover dependency on its own.
Contradictory hypotheses make a theorem true and worthless: the proof is
*genuinely valid*, the kernel is *entirely satisfied*, and the theorem
still says nothing, because anything follows from a contradiction. No
amount of checking the answers finds it -- every participant "solves" it --
and an LLM asked "are these hypotheses contradictory" will guess, since
the honest answer can require real mathematics. A prover either derives
``False`` or it does not.

Run this over the problem folder before publishing it. The counterpart
question -- does the statement mean what the prose says -- is
``ftp_eval.autoformalization``, which is pure judge and needs no prover.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from ...backends.types import ProbeKind, StatementTask, Status
from ...backends.soundness import STATEMENT_HACK_CLASSES, screen_source

__all__ = [
    "HealthKind",
    "HealthCheck",
    "ProblemHealth",
    "check_problem_health",
    "format_health_summary",
]


class HealthKind(str, Enum):
    """Which question was asked."""

    #: Text only, so it runs without a prover.
    ASSUMES_NOTHING = "assumes_nothing"
    ELABORATES = "elaborates"
    NON_TRIVIAL = "non_trivial"
    NON_VACUOUS = "non_vacuous"
    GOLD_EQUIVALENT = "gold_equivalent"


@dataclass(frozen=True)
class HealthCheck:
    """One question's answer.

    ``passed`` is deliberately three-valued. ``None`` means the probe did
    not run -- no prover, or the backend cannot build that probe for this
    language -- and must never be read as either verdict. A problem set
    reported healthy because nothing was asked is the failure this type
    exists to prevent.
    """

    kind: HealthKind
    passed: bool | None
    detail: str = ""
    #: A failure that makes the problem ungradeable rather than merely odd.
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "passed": self.passed,
            "detail": self.detail,
            "fatal": self.fatal,
        }


@dataclass
class ProblemHealth:
    """Every answer for one problem."""

    problem_id: str
    checks: tuple[HealthCheck, ...] = ()
    wall_time_s: float = 0.0
    #: The probe sources and prover output, kept so a verdict can be re-run.
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> tuple[HealthCheck, ...]:
        return tuple(c for c in self.checks if c.passed is False)

    @property
    def ungradeable(self) -> bool:
        """A problem no participant could be graded on fairly."""
        return any(c.passed is False and c.fatal for c in self.failures)

    @property
    def suspect(self) -> bool:
        """Something failed, but not fatally. Worth a human's eyes."""
        return bool(self.failures) and not self.ungradeable

    @property
    def checked(self) -> bool:
        """Whether anything at all was established."""
        return any(c.passed is not None for c in self.checks)

    @property
    def probed(self) -> bool:
        """Whether a *prover* question was answered.

        Distinct from ``checked`` because the text check always runs: a
        problem set with no prover has ``checked`` true and ``probed``
        false, and reporting it as healthy would hide that vacuity -- the
        defect that matters most -- was never looked for.
        """
        return any(
            c.passed is not None and c.kind is not HealthKind.ASSUMES_NOTHING
            for c in self.checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "ungradeable": self.ungradeable,
            "suspect": self.suspect,
            "checked": self.checked,
            "probed": self.probed,
            "checks": [c.to_dict() for c in self.checks],
            "wall_time_s": self.wall_time_s,
            "raw": dict(self.raw),
        }

    def format_text(self) -> str:
        if self.ungradeable:
            mark = "UNGRADEABLE"
        elif self.suspect:
            mark = "SUSPECT    "
        elif self.probed:
            mark = "ok         "
        else:
            mark = "not checked"
        failures = self.failures
        return "%s %-16s %s" % (
            mark,
            self.problem_id[:16],
            failures[0].detail[:76] if failures else "",
        )


def check_problem_health(
    verifier: Any,
    task: StatementTask,
    *,
    timeout_s: float = 120.0,
    check_trivial: bool = True,
    check_vacuous: bool = True,
    check_gold: bool = True,
) -> ProblemHealth:
    """Ask all five questions. Never raises.

    ``verifier`` of ``None`` is allowed: ``assumes_nothing`` still runs,
    since it is text only, and the prover questions come back ``None``.
    The caller asked for no prover and the result says so rather than
    implying a clean bill of health.
    """
    started = time.monotonic()
    raw: dict[str, Any] = {}
    # Free and deterministic, so it runs first and runs always.
    checks: list[HealthCheck] = [_assumes_nothing(task)]

    if verifier is None:
        checks.extend(
            HealthCheck(kind, None, "no prover configured, so this was not checked")
            for kind in (HealthKind.ELABORATES, HealthKind.NON_TRIVIAL, HealthKind.NON_VACUOUS)
        )
        return ProblemHealth(task.task_id, tuple(checks), time.monotonic() - started, raw)

    def probe(kind: ProbeKind) -> Status | None:
        source = verifier.build_probe(task, kind)
        if source is None:
            return None
        result = verifier.probe(task, source, timeout_s=timeout_s)
        raw["probe_%s" % kind.value] = {
            "status": result.status.value,
            "source": source,
            "diagnostics": [d.to_dict() for d in result.diagnostics[:3]],
        }
        return result.status

    elaborates = probe(ProbeKind.ELABORATES)
    if elaborates is None:
        checks.append(
            HealthCheck(
                HealthKind.ELABORATES,
                None,
                "backend %r cannot build an elaboration probe for %s"
                % (getattr(verifier, "name", "?"), task.language),
            )
        )
    elif elaborates is Status.VERIFIED:
        checks.append(
            HealthCheck(HealthKind.ELABORATES, True, "statement typechecks on its own")
        )
    elif elaborates in (Status.ERROR, Status.SKIPPED):
        checks.append(
            HealthCheck(
                HealthKind.ELABORATES, None, "probe could not run (%s)" % elaborates.value
            )
        )
    else:
        # Decisive: a statement that does not typecheck cannot be graded,
        # and the remaining probes would all fail for the same reason.
        checks.append(
            HealthCheck(
                HealthKind.ELABORATES,
                False,
                "statement does not typecheck, so nobody can be graded on it (%s)"
                % elaborates.value,
                fatal=True,
            )
        )
        for kind in (HealthKind.NON_TRIVIAL, HealthKind.NON_VACUOUS):
            checks.append(
                HealthCheck(kind, None, "skipped: the statement does not typecheck")
            )
        return ProblemHealth(task.task_id, tuple(checks), time.monotonic() - started, raw)

    if check_trivial:
        checks.append(_trivial(probe))
    if check_vacuous:
        checks.append(_vacuous(probe))
    if check_gold and task.gold_formal_statement:
        checks.append(_gold(probe))

    return ProblemHealth(task.task_id, tuple(checks), time.monotonic() - started, raw)


def _assumes_nothing(task: StatementTask) -> HealthCheck:
    """A problem must not assume what it asks for.

    An `axiom`, an `opaque` constant, or `set_option autoImplicit true`
    makes a problem wrong before anyone answers it. Screened for
    ``STATEMENT_HACK_CLASSES``, which deliberately excludes placeholders --
    a problem file's `sorry` is the hole a participant fills -- and
    `variable` bindings, which are ordinary Lean.

    Needs no prover, which is why it is the one health check that always
    runs.
    """
    report = screen_source(
        task.formal_statement,
        task.language,
        required_statement=None,
        allowed_imports=None,
        classes=STATEMENT_HACK_CLASSES,
        subject="statement",
    )
    if report.ok:
        return HealthCheck(
            HealthKind.ASSUMES_NOTHING, True, "assumes nothing it should prove"
        )
    return HealthCheck(
        HealthKind.ASSUMES_NOTHING, False, "; ".join(report.violations), fatal=True
    )


def _trivial(probe: Any) -> HealthCheck:
    """A goal a one-liner closes is usually a lost formalization.

    Not always -- some problems really are one ``simp`` away -- so this is
    reported as suspect rather than fatal. What it reliably catches is the
    degenerate case: a statement collapsed to ``True``, or to a closed
    arithmetic identity ``decide`` evaluates.
    """
    outcome = probe(ProbeKind.TRIVIAL)
    if outcome is None:
        return HealthCheck(HealthKind.NON_TRIVIAL, None, "no triviality probe available")
    if outcome in (Status.ERROR, Status.SKIPPED):
        return HealthCheck(
            HealthKind.NON_TRIVIAL, None, "probe could not run (%s)" % outcome.value
        )
    if outcome is Status.VERIFIED:
        return HealthCheck(
            HealthKind.NON_TRIVIAL,
            False,
            "a single cheap tactic closes this goal, which usually means the "
            "formalization lost the content of the problem",
        )
    return HealthCheck(HealthKind.NON_TRIVIAL, True, "not closable by a cheap tactic")


def _vacuous(probe: Any) -> HealthCheck:
    """Contradictory hypotheses make a theorem true and worthless.

    The one defect the kernel cannot help with at all: the proof is
    genuinely valid, the kernel is entirely satisfied, and the theorem
    still says nothing. It can only be caught by interrogating the
    statement, which is why it is here and not among the proof checks.
    """
    outcome = probe(ProbeKind.VACUOUS)
    if outcome is None:
        return HealthCheck(
            HealthKind.NON_VACUOUS,
            None,
            "no vacuity probe available (the statement has no hypotheses, or does "
            "not parse)",
        )
    if outcome in (Status.ERROR, Status.SKIPPED):
        return HealthCheck(
            HealthKind.NON_VACUOUS, None, "probe could not run (%s)" % outcome.value
        )
    if outcome is Status.VERIFIED:
        return HealthCheck(
            HealthKind.NON_VACUOUS,
            False,
            "`False` is derivable from the hypotheses, so the statement is vacuously "
            "true and every answer to it proves nothing",
            fatal=True,
        )
    return HealthCheck(HealthKind.NON_VACUOUS, True, "hypotheses are not contradictory")


def _gold(probe: Any) -> HealthCheck:
    """Compare against a reference formalization, via the prover.

    Asking the prover for an ``Iff`` is far stronger than comparing text:
    it accepts a differently-phrased but equivalent statement, which
    textual comparison would wrongly reject.
    """
    outcome = probe(ProbeKind.GOLD_EQUIVALENT)
    if outcome is None:
        return HealthCheck(
            HealthKind.GOLD_EQUIVALENT,
            None,
            "cannot compare to the reference: the two statements have different "
            "binders, so no equivalence can be stated between them",
        )
    if outcome in (Status.ERROR, Status.SKIPPED):
        return HealthCheck(
            HealthKind.GOLD_EQUIVALENT, None, "probe could not run (%s)" % outcome.value
        )
    if outcome is Status.VERIFIED:
        return HealthCheck(
            HealthKind.GOLD_EQUIVALENT, True, "provably equivalent to the reference statement"
        )
    # Failing to *prove* the equivalence is not proof of inequivalence: the
    # tactics tried are weak, and a true equivalence can be hard.
    return HealthCheck(
        HealthKind.GOLD_EQUIVALENT,
        None,
        "equivalence with the reference could not be proved by the cheap tactics "
        "tried; this is not evidence that the two differ",
    )


def format_health_summary(results: Sequence[ProblemHealth]) -> str:
    """One block for the setter, ending with what was not established."""
    probed = sum(1 for r in results if r.probed)
    lines = [
        "problem health: %d problem(s), %d probed by a prover" % (len(results), probed)
    ]
    ungradeable = [r for r in results if r.ungradeable]
    suspect = [r for r in results if r.suspect]
    unprobed = [r for r in results if not r.probed]

    counts: dict[str, int] = {}
    for result in results:
        for check in result.failures:
            counts[check.kind.value] = counts.get(check.kind.value, 0) + 1
    if counts:
        lines.append("  failed: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))

    if ungradeable:
        lines.append(
            "  UNGRADEABLE (%d): %s"
            % (len(ungradeable), ", ".join(r.problem_id for r in ungradeable[:10]))
        )
        lines.append(
            "    These cannot be graded fairly. A vacuous statement is the worst "
            "case: every answer to it is valid and none proves anything."
        )
    if suspect:
        lines.append(
            "  suspect (%d): %s"
            % (len(suspect), ", ".join(r.problem_id for r in suspect[:10]))
        )
    if unprobed:
        lines.append(
            "  NOT CHECKED (%d): no prover probe ran for these, so nothing here is "
            "evidence that they are sound -- in particular nothing looked for a "
            "vacuously true statement." % len(unprobed)
        )
    return "\n".join(lines)
