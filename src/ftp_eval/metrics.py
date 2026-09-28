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

from .types import ErrorKind, Status, VerificationResult

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
    by_split: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_task: list[TaskOutcome] = field(default_factory=list)

    @property
    def solve_rate(self) -> float:
        """Fraction of tasks solved by at least one sample (pass@n)."""
        return self.solved_tasks / self.tasks if self.tasks else 0.0

    @property
    def attempt_pass_rate(self) -> float:
        """Fraction of scoreable *attempts* that verified."""
        return self.verified / self.scoreable_attempts if self.scoreable_attempts else 0.0

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
            "integrity_ok": self.integrity_ok,
            "timing": {
                "total_wall_time_s": round(self.total_wall_time_s, 3),
                "median_wall_time_s": round(self.median_wall_time_s, 3),
            },
            "by_split": self.by_split,
        }

    def format_text(self) -> str:
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
            lines.append("SOUNDNESS violations (not counted as passes):")
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
        lines.append("wall time: %.1fs total, %.2fs median/attempt" % (self.total_wall_time_s, self.median_wall_time_s))
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
    times: list[float] = []
    by_split_results: dict[str, list[VerificationResult]] = defaultdict(list)

    for r in results:
        summary.attempts += 1
        summary.total_wall_time_s += r.wall_time_s
        times.append(r.wall_time_s)
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
        if r.split:
            by_split_results[r.split].append(r)

    summary.tasks = len(outcomes)
    summary.solved_tasks = sum(1 for o in outcomes.values() if o.solved)
    summary.error_kinds = dict(error_kinds)
    summary.soundness_violations = dict(violations)
    summary.median_wall_time_s = _median(times)

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
