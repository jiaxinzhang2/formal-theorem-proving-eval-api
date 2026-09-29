"""Scoring: pass@k, and the breakdowns you need to trust a pass@k.

Two things here are easy to get wrong and are therefore done explicitly:

1. ``pass@k`` uses the unbiased estimator from Chen et al. 2021
   (*Evaluating Large Language Models Trained on Code*), not
   "did any of my k samples pass". With n samples per task, the naive
   version answers a different question and is biased upward whenever
   n != k.
2. Harness errors are not silent failures. ``ERROR`` and ``SKIPPED``
   attempts are excluded from the pass-rate denominator and reported on
   their own line, so a broken toolchain looks like a broken toolchain
   instead of a weak model.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .modes import (
    FailureMode,
    FailureModeStats,
    SuccessMode,
    SuccessModeStats,
    aggregate_failure_modes,
    aggregate_success_modes,
)
from ...stats import (
    Correlation,
    Distribution,
    correlate_with_success,
    distribution,
    format_correlations,
)
from .structure import (
    NUMERIC_FIELDS,
    SampleDuplication,
    StructureStats,
    aggregate_structure,
    sample_duplication,
)
from ...soundness import parse_label
from .tactics import TacticStats, tactic_stats
from ...types import ErrorKind, Status, VerificationResult

__all__ = ["pass_at_k", "estimate_pass_at_k", "TaskOutcome", "Summary", "summarize", "compare"]


def estimate_pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k for one task: n samples drawn, c of them correct.

    Equals ``1 - C(n-c, k) / C(n, k)``, computed as a running product to
    stay exact-ish for large n instead of overflowing binomials.
    """
    if k <= 0:
        raise ValueError("k must be >= 1")
    if n <= 0:
        raise ValueError("n must be >= 1")
    if c < 0 or c > n:
        raise ValueError("c must satisfy 0 <= c <= n")
    if k > n:
        # Asking for pass@k with fewer than k samples is unanswerable; the
        # caller is told rather than handed a plausible-looking number.
        raise ValueError("pass@%d needs at least %d samples, got %d" % (k, k, n))
    if n - c < k:
        return 1.0
    product = 1.0
    for i in range(k):
        product *= (n - c - i) / (n - i)
    return 1.0 - product


def pass_at_k(results: Iterable[VerificationResult], k: int) -> float | None:
    """Mean unbiased pass@k over tasks, or None if no task has k samples.

    Tasks with fewer than k scoreable samples are dropped rather than
    padded with failures, which would bias the estimate downward.
    """
    per_task = _group_scoreable(results)
    values = []
    for n, c in per_task.values():
        if n >= k:
            values.append(estimate_pass_at_k(n, c, k))
    if not values:
        return None
    return sum(values) / len(values)


def _group_scoreable(results: Iterable[VerificationResult]) -> dict[str, tuple[int, int]]:
    """task_id -> (scoreable samples, verified samples)."""
    n_by_task: Counter[str] = Counter()
    c_by_task: Counter[str] = Counter()
    for r in results:
        if not r.status.counts_toward_pass_rate:
            continue
        n_by_task[r.task_id] += 1
        if r.verified:
            c_by_task[r.task_id] += 1
    return {t: (n_by_task[t], c_by_task[t]) for t in n_by_task}


@dataclass
class TaskOutcome:
    """Per-task rollup, for finding the tasks nobody can do."""

    task_id: str
    samples: int = 0
    verified: int = 0
    rejected: int = 0
    errored: int = 0
    split: str | None = None
    first_error_kind: ErrorKind | None = None
    fastest_verified_s: float | None = None

    @property
    def solved(self) -> bool:
        return self.verified > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "split": self.split,
            "samples": self.samples,
            "verified": self.verified,
            "rejected": self.rejected,
            "errored": self.errored,
            "solved": self.solved,
            "first_error_kind": self.first_error_kind.value if self.first_error_kind else None,
            "fastest_verified_s": self.fastest_verified_s,
        }


@dataclass
class Summary:
    """Everything needed to report a run honestly."""

    backend: str | None = None
    model: str | None = None
    tasks: int = 0
    attempts: int = 0
    scoreable_attempts: int = 0
    verified: int = 0
    rejected: int = 0
    failed: int = 0
    timeout: int = 0
    errored: int = 0
    skipped: int = 0
    solved_tasks: int = 0
    pass_at: dict[int, float] = field(default_factory=dict)
    error_kinds: dict[str, int] = field(default_factory=dict)
    soundness_violations: dict[str, int] = field(default_factory=dict)
    total_wall_time_s: float = 0.0
    median_wall_time_s: float = 0.0
    #: Distribution of the prover's own compile time, when backends report
    #: it. Usually the dominant cost, and the one worth tuning; comparing
    #: its total against ``total_wall_time_s`` shows harness overhead.
    compile_time: Distribution = field(default_factory=Distribution)
    #: Compile-time distribution restricted to accepted proofs. A failure
    #: that times out costs the full budget, so pooling the two hides how
    #: long a *successful* check actually takes.
    compile_time_verified: Distribution = field(default_factory=Distribution)
    by_split: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Per-sample-position pass rate, ``{0: 0.42, 1: 0.39, ...}``. A steep
    #: decline means later samples are much worse, which is worth knowing
    #: before you pay for k=10; a flat line means extra samples are
    #: genuinely independent draws.
    by_sample_index: dict[int, float] = field(default_factory=dict)
    per_task: list[TaskOutcome] = field(default_factory=list)
    tactics: TacticStats = field(default_factory=TacticStats)
    structure: StructureStats = field(default_factory=StructureStats)
    duplication: SampleDuplication = field(default_factory=SampleDuplication)
    #: Which structural metrics actually track success, ranked. The reason
    #: for collecting many metrics is to find the ones that carry signal;
    #: this is that search. Association, not causation.
    #: Why failures failed, at the granularity that says what to fix, plus
    #: the attribution cut (model vs budget vs harness).
    failure_modes: FailureModeStats = field(default_factory=FailureModeStats)
    #: What kind of proofs passed. Same pass rate, different shapes, is a
    #: real difference between models and this is where it shows.
    success_modes: SuccessModeStats = field(default_factory=SuccessModeStats)
    correlations: list[Correlation] = field(default_factory=list)
    #: Per-model rollup when a run mixes models, so one file can hold a
    #: comparison rather than needing one file per model.
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: How many samples were drawn before the first success, over tasks
    #: that were eventually solved. A low median means extra samples are
    #: mostly wasted; a high one means k matters.
    samples_to_first_success: Distribution = field(default_factory=Distribution)
    #: Reward-hacking classes seen, e.g. ``{"placeholder": 3, "new_axiom": 1}``.
    hack_classes: dict[str, int] = field(default_factory=dict)
    #: The specific trick, e.g. ``{"lean.sorry": 14, "kernel.sorry_ax": 2}``.
    #: More actionable than the class: it names what to forbid next.
    hack_patterns: dict[str, int] = field(default_factory=dict)

    @property
    def solve_rate(self) -> float:
        """Fraction of tasks solved by at least one sample (pass@n)."""
        return self.solved_tasks / self.tasks if self.tasks else 0.0

    @property
    def attempt_pass_rate(self) -> float:
        """Fraction of scoreable *attempts* that verified."""
        return self.verified / self.scoreable_attempts if self.scoreable_attempts else 0.0

    @property
    def seconds_per_solved_task(self) -> float | None:
        """Wall clock spent per task actually solved.

        The efficiency number that matters when comparing configurations:
        a setup that solves 5% more at four times the cost is a different
        trade from one that solves 5% more for free. ``None`` when nothing
        was solved, rather than infinity.
        """
        if not self.solved_tasks:
            return None
        return self.total_wall_time_s / self.solved_tasks

    @property
    def integrity_ok(self) -> bool:
        """Whether the run is clean enough for its numbers to be quoted.

        Any harness/toolchain error, or any attempt that the prover
        accepted but the soundness screen rejected, means the headline
        figure needs a caveat.
        """
        return self.errored == 0 and self.rejected == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "model": self.model,
            "tasks": self.tasks,
            "attempts": self.attempts,
            "scoreable_attempts": self.scoreable_attempts,
            "counts": {
                "verified": self.verified,
                "rejected": self.rejected,
                "failed": self.failed,
                "timeout": self.timeout,
                "errored": self.errored,
                "skipped": self.skipped,
            },
            "solved_tasks": self.solved_tasks,
            "solve_rate": round(self.solve_rate, 6),
            "attempt_pass_rate": round(self.attempt_pass_rate, 6),
            "pass_at": {"pass@%d" % k: round(v, 6) for k, v in sorted(self.pass_at.items())},
            "error_kinds": self.error_kinds,
            "soundness_violations": self.soundness_violations,
            "hack_classes": self.hack_classes,
            "hack_patterns": self.hack_patterns,
            "tactics": self.tactics.to_dict(),
            "structure": self.structure.to_dict(),
            "duplication": self.duplication.to_dict(),
            "failure_modes": self.failure_modes.to_dict(),
            "success_modes": self.success_modes.to_dict(),
            "integrity_ok": self.integrity_ok,
            "timing": {
                "total_wall_time_s": round(self.total_wall_time_s, 3),
                "median_wall_time_s": round(self.median_wall_time_s, 3),
                "seconds_per_solved_task": (
                    None
                    if self.seconds_per_solved_task is None
                    else round(self.seconds_per_solved_task, 3)
                ),
                "compile_time": self.compile_time.to_dict(),
                "compile_time_verified": self.compile_time_verified.to_dict(),
                "harness_overhead_s": round(
                    max(0.0, self.total_wall_time_s - self.compile_time.total), 3
                ),
            },
            "by_split": self.by_split,
            "by_model": self.by_model,
            "by_sample_index": {str(k): round(v, 6) for k, v in self.by_sample_index.items()},
            "correlations": [c.to_dict() for c in self.correlations],
            "samples_to_first_success": self.samples_to_first_success.to_dict(),
        }

    def format_text(self, *, include_tactics: bool = False) -> str:
        """Human-readable report for the CLI."""
        lines = []
        head = "backend=%s" % (self.backend or "?")
        if self.model:
            head += "  model=%s" % self.model
        lines.append(head)
        lines.append(
            "tasks=%d  attempts=%d  scoreable=%d" % (self.tasks, self.attempts, self.scoreable_attempts)
        )
        lines.append(
            "verified=%d  failed=%d  timeout=%d  rejected=%d  error=%d  skipped=%d"
            % (self.verified, self.failed, self.timeout, self.rejected, self.errored, self.skipped)
        )
        lines.append(
            "solve rate (>=1 sample) = %.1f%% (%d/%d)"
            % (100 * self.solve_rate, self.solved_tasks, self.tasks)
        )
        for k, v in sorted(self.pass_at.items()):
            lines.append("pass@%-3d = %.1f%%" % (k, 100 * v))
        if self.error_kinds:
            ranked = sorted(self.error_kinds.items(), key=lambda kv: -kv[1])
            lines.append("error kinds: " + ", ".join("%s=%d" % kv for kv in ranked))
        if self.soundness_violations:
            lines.append(
                "REWARD HACKING detected (%d attempt(s) rejected, not counted as passes):"
                % self.rejected
            )
            if self.hack_classes:
                ranked = sorted(self.hack_classes.items(), key=lambda kv: -kv[1])
                lines.append("  by class:   " + ", ".join("%s=%d" % kv for kv in ranked))
            if self.hack_patterns:
                ranked = sorted(self.hack_patterns.items(), key=lambda kv: -kv[1])
                lines.append("  by trick:   " + ", ".join("%s=%d" % kv for kv in ranked))
            for reason, count in sorted(self.soundness_violations.items(), key=lambda kv: -kv[1]):
                lines.append("  %4d x %s" % (count, reason))
        if self.errored:
            lines.append(
                "WARNING: %d attempt(s) never got a verdict; they are excluded from the "
                "denominator, so fix the toolchain before quoting these numbers." % self.errored
            )
        if self.by_split:
            lines.append("by split:")
            for split, stats in sorted(self.by_split.items()):
                lines.append(
                    "  %-12s solve=%5.1f%%  verified=%d/%d"
                    % (split, 100 * stats["solve_rate"], stats["verified"], stats["scoreable_attempts"])
                )
        lines.append(
            "wall time: %.1fs total, %.2fs median/attempt%s"
            % (
                self.total_wall_time_s,
                self.median_wall_time_s,
                ""
                if self.seconds_per_solved_task is None
                else ", %.1fs per solved task" % self.seconds_per_solved_task,
            )
        )
        if self.compile_time.count:
            lines.append(
                "prover time: %.1fs total, median %.2fs, p90 %.2fs, max %.1fs "
                "(%.1fs harness overhead)"
                % (
                    self.compile_time.total,
                    self.compile_time.median,
                    self.compile_time.p90,
                    self.compile_time.maximum,
                    max(0.0, self.total_wall_time_s - self.compile_time.total),
                )
            )
            if self.compile_time_verified.count:
                lines.append(
                    "  accepted proofs compile in median %.2fs, p90 %.2fs"
                    % (self.compile_time_verified.median, self.compile_time_verified.p90)
                )
        if len(self.by_sample_index) > 1:
            lines.append(
                "pass rate by sample position: "
                + ", ".join(
                    "#%d=%.0f%%" % (i, 100 * rate)
                    for i, rate in sorted(self.by_sample_index.items())
                )
            )
        if self.duplication.undermines_pass_at_k and self.pass_at:
            detail = (
                "; %d task(s) returned k identical proofs" % self.duplication.tasks_all_identical
                if self.duplication.tasks_all_identical
                else ""
            )
            lines.append(
                "WARNING: %.0f%% of samples repeat another sample of the same task%s. "
                "pass@k assumes k independent draws, so the k>1 figures above claim more "
                "precision than the data supports."
                % (100 * self.duplication.mean_duplicate_fraction, detail)
            )
        if self.by_model:
            lines.append("by model:")
            for model, stats in sorted(
                self.by_model.items(), key=lambda kv: -kv[1]["solve_rate"]
            ):
                lines.append(
                    "  %-24s solve=%5.1f%%  verified=%d/%d  rejected=%d"
                    % (
                        model[:24],
                        100 * stats["solve_rate"],
                        stats["verified"],
                        stats["scoreable_attempts"],
                        stats["rejected"],
                    )
                )
        if self.samples_to_first_success.count:
            d = self.samples_to_first_success
            lines.append(
                "samples drawn before first success: median %.1f, p90 %.1f, max %.0f "
                "(over %d solved task(s))" % (d.median, d.p90, d.maximum, d.count)
            )
        if self.failure_modes.budget_fraction >= 0.10:
            lines.append(
                "WARNING: %.0f%% of failures are budget, not capability (truncation, "
                "timeouts, heartbeat limits) -- see the failure-mode table."
                % (100 * self.failure_modes.budget_fraction)
            )
        if include_tactics:
            if self.failure_modes.failures:
                lines.append("")
                lines.append(self.failure_modes.format_text())
            if self.success_modes.successes:
                lines.append("")
                lines.append(self.success_modes.format_text())
            if self.tactics.proofs_measured:
                lines.append("")
                lines.append(self.tactics.format_text())
            if self.structure.proofs:
                lines.append("")
                lines.append(self.structure.format_text())
            if self.correlations:
                lines.append("")
                lines.append(format_correlations(self.correlations))
        return "\n".join(lines)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def summarize(
    results: Iterable[VerificationResult],
    *,
    ks: Sequence[int] = (1,),
    include_per_task: bool = False,
    _split_depth: int = 0,
) -> Summary:
    """Roll a result stream up into a :class:`Summary`.

    ``ks`` values larger than the available sample count are skipped
    rather than approximated.
    """
    results = list(results)
    summary = Summary()
    if not results:
        return summary

    backends = {r.backend for r in results}
    models = {r.model for r in results if r.model}
    summary.backend = next(iter(backends)) if len(backends) == 1 else "+".join(sorted(backends))
    summary.model = next(iter(models)) if len(models) == 1 else (
        "+".join(sorted(models)) if models else None
    )

    outcomes: dict[str, TaskOutcome] = {}
    error_kinds: Counter[str] = Counter()
    violations: Counter[str] = Counter()
    hack_classes: Counter[str] = Counter()
    hack_patterns: Counter[str] = Counter()
    tactic_entries: list[tuple[Sequence[str], bool, bool]] = []
    structure_entries: list[tuple[Mapping[str, Any], bool, bool]] = []
    samples_by_task: dict[str, list[str]] = {}
    times: list[float] = []
    compile_times: list[float] = []
    compile_times_verified: list[float] = []
    by_index_total: Counter[int] = Counter()
    by_index_verified: Counter[int] = Counter()
    correlation_entries: list[tuple[Mapping[str, Any], bool]] = []
    failure_entries: list[tuple[FailureMode | None, Any, str | None, int, int]] = []
    success_entries: list[tuple[SuccessMode | None, str | None]] = []
    by_model_results: dict[str, list[VerificationResult]] = defaultdict(list)
    first_success: dict[str, int] = {}
    by_split_results: dict[str, list[VerificationResult]] = defaultdict(list)

    for r in results:
        summary.attempts += 1
        summary.total_wall_time_s += r.wall_time_s
        times.append(r.wall_time_s)
        if r.compile_time_s is not None:
            compile_times.append(r.compile_time_s)
            if r.status is Status.VERIFIED:
                compile_times_verified.append(r.compile_time_s)
        if r.status.counts_toward_pass_rate:
            by_index_total[r.sample_index] += 1
            if r.status is Status.VERIFIED:
                by_index_verified[r.sample_index] += 1
        outcome = outcomes.setdefault(r.task_id, TaskOutcome(task_id=r.task_id, split=r.split))

        if r.status is Status.VERIFIED:
            summary.verified += 1
            outcome.verified += 1
            if outcome.fastest_verified_s is None or r.wall_time_s < outcome.fastest_verified_s:
                outcome.fastest_verified_s = round(r.wall_time_s, 3)
        elif r.status is Status.REJECTED:
            summary.rejected += 1
            outcome.rejected += 1
        elif r.status is Status.FAILED:
            summary.failed += 1
        elif r.status is Status.TIMEOUT:
            summary.timeout += 1
        elif r.status is Status.ERROR:
            summary.errored += 1
            outcome.errored += 1
        elif r.status is Status.SKIPPED:
            summary.skipped += 1

        if r.status.counts_toward_pass_rate:
            summary.scoreable_attempts += 1
            outcome.samples += 1
        if r.error_kind is not None:
            error_kinds[r.error_kind.value] += 1
            if outcome.first_error_kind is None:
                outcome.first_error_kind = r.error_kind
        for v in r.soundness.violations:
            violations[v] += 1
            # Violations are tagged "[class:pattern_id] message" by the
            # screen, so both roll up without a second lookup.
            hack_class, pattern_id = parse_label(v)
            if hack_class:
                hack_classes[hack_class] += 1
            if pattern_id:
                hack_patterns[pattern_id] += 1
        tactic_entries.append(
            (r.tactics, r.status is Status.VERIFIED, r.status.counts_toward_pass_rate)
        )
        structure_entries.append(
            (r.structure, r.status is Status.VERIFIED, r.status.counts_toward_pass_rate)
        )
        if r.status.counts_toward_pass_rate and r.structure:
            correlation_entries.append((r.structure, r.status is Status.VERIFIED))
        if r.model:
            by_model_results[r.model].append(r)
        if r.status is Status.VERIFIED:
            best = first_success.get(r.task_id)
            if best is None or r.sample_index < best:
                first_success[r.task_id] = r.sample_index
        # Fingerprints stand in for proof text, which results do not carry.
        fingerprint = str((r.structure or {}).get("chars", "")) + "|" + "\u0000".join(r.tactics)
        samples_by_task.setdefault(r.task_id, []).append(fingerprint)
        if r.failure_mode:
            try:
                mode: FailureMode | None = FailureMode(r.failure_mode)
            except ValueError:
                mode = FailureMode.UNCLASSIFIED
            first_line = next((d.line for d in r.diagnostics if d.line), 0) or 0
            failure_entries.append(
                (
                    mode,
                    r.diagnostics,
                    r.model,
                    first_line,
                    int((r.structure or {}).get("lines") or 0),
                )
            )
        if r.success_mode:
            try:
                success_entries.append((SuccessMode(r.success_mode), r.model))
            except ValueError:
                success_entries.append((SuccessMode.UNCLASSIFIED, r.model))
        if r.split:
            by_split_results[r.split].append(r)

    summary.tasks = len(outcomes)
    summary.solved_tasks = sum(1 for o in outcomes.values() if o.solved)
    summary.error_kinds = dict(error_kinds)
    summary.soundness_violations = dict(violations)
    summary.hack_classes = dict(hack_classes)
    summary.hack_patterns = dict(hack_patterns)
    summary.tactics = tactic_stats(tactic_entries)
    summary.structure = aggregate_structure(structure_entries)
    summary.duplication = sample_duplication(samples_by_task)
    summary.median_wall_time_s = _median(times)
    summary.compile_time = distribution(compile_times)
    summary.compile_time_verified = distribution(compile_times_verified)
    summary.by_sample_index = {
        index: by_index_verified[index] / total
        for index, total in sorted(by_index_total.items())
        if total
    }
    summary.failure_modes = aggregate_failure_modes(failure_entries)
    summary.success_modes = aggregate_success_modes(success_entries)
    summary.correlations = correlate_with_success(correlation_entries, fields=NUMERIC_FIELDS)
    # sample_index is 0-based; "samples drawn" is one more than that.
    summary.samples_to_first_success = distribution(
        [index + 1 for index in first_success.values()]
    )

    if _split_depth == 0 and len(by_model_results) > 1:
        for model, model_results in by_model_results.items():
            sub = summarize(model_results, ks=ks, _split_depth=1)
            summary.by_model[model] = {
                "tasks": sub.tasks,
                "verified": sub.verified,
                "rejected": sub.rejected,
                "errored": sub.errored,
                "scoreable_attempts": sub.scoreable_attempts,
                "solve_rate": round(sub.solve_rate, 6),
                "integrity_ok": sub.integrity_ok,
                "pass_at": {"pass@%d" % kk: round(vv, 6) for kk, vv in sorted(sub.pass_at.items())},
            }

    for k in ks:
        value = pass_at_k(results, k)
        if value is not None:
            summary.pass_at[k] = value

    # One level only: a per-split summary contains a single split, so
    # recursing again would rebuild the same sub-summary forever.
    for split, split_results in by_split_results.items() if _split_depth == 0 else ():
        sub = summarize(split_results, ks=ks, _split_depth=1)
        summary.by_split[split] = {
            "tasks": sub.tasks,
            "verified": sub.verified,
            "scoreable_attempts": sub.scoreable_attempts,
            "solve_rate": round(sub.solve_rate, 6),
            "pass_at": {"pass@%d" % kk: round(vv, 6) for kk, vv in sorted(sub.pass_at.items())},
        }

    if include_per_task:
        summary.per_task = sorted(outcomes.values(), key=lambda o: o.task_id)
    return summary


def compare(
    summaries: Mapping[str, Summary],
    *,
    k: int = 1,
) -> str:
    """A leaderboard over several named summaries, worst caveats included."""
    if not summaries:
        return "(nothing to compare)"
    rows = []
    for name, s in summaries.items():
        score = s.pass_at.get(k)
        rows.append((score if score is not None else -1.0, name, s))
    rows.sort(key=lambda row: -row[0])
    out = ["%-28s %9s %9s %8s %s" % ("name", "pass@%d" % k, "solve", "errors", "clean")]
    for score, name, s in rows:
        out.append(
            "%-28s %8s %8.1f%% %8d %s"
            % (
                name[:28],
                "n/a" if score < 0 else "%.1f%%" % (100 * score),
                100 * s.solve_rate,
                s.errored,
                "yes" if s.integrity_ok else "NO",
            )
        )
    return "\n".join(out)
