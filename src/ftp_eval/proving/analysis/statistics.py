"""Distributions and correlations used by proof analysis.

Percentiles sit beside the mean everywhere these are used because the
quantities they summarize are skewed -- a handful of enormous proofs pull a
mean far away from what a typical one looks like.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Distribution",
    "distribution",
    "point_biserial",
    "Correlation",
    "correlate_with_success",
    "format_correlations",
]


@dataclass(frozen=True)
class Distribution:
    """Standard summary statistics for one metric over a run.

    Percentiles are reported alongside the mean because these metrics are
    skewed: a handful of enormous proofs pull a mean far away from what a
    typical proof looks like, and the median plus p90 say more than the
    mean and standard deviation do on their own.
    """

    count: int = 0
    mean: float = 0.0
    median: float = 0.0
    stdev: float = 0.0
    minimum: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    p90: float = 0.0
    maximum: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean": round(self.mean, 3),
            "median": round(self.median, 3),
            "stdev": round(self.stdev, 3),
            "min": round(self.minimum, 3),
            "p25": round(self.p25, 3),
            "p75": round(self.p75, 3),
            "p90": round(self.p90, 3),
            "max": round(self.maximum, 3),
            "total": round(self.total, 3),
        }


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile. Empty input gives 0.0."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return float(sorted_values[low] * (1 - weight) + sorted_values[high] * weight)


def distribution(values: Iterable[float]) -> Distribution:
    """Summary statistics for one metric."""
    data = [float(v) for v in values]
    if not data:
        return Distribution()
    ordered = sorted(data)
    return Distribution(
        count=len(data),
        mean=sum(data) / len(data),
        median=statistics.median(ordered),
        stdev=statistics.pstdev(ordered) if len(ordered) > 1 else 0.0,
        minimum=ordered[0],
        p25=_percentile(ordered, 0.25),
        p75=_percentile(ordered, 0.75),
        p90=_percentile(ordered, 0.90),
        maximum=ordered[-1],
        total=sum(data),
    )





def point_biserial(values: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    """Correlation between a numeric metric and a pass/fail outcome.

    Pearson's r with a binary second variable. Returns ``None`` when it
    is undefined -- fewer than three points, no variation in the metric,
    or every outcome the same -- rather than a number that would read as
    "no relationship" when the truth is "not computable".

    **This is an association, not a cause.** ``named_steps`` correlating
    with success may only mean that the tasks a model can solve are the
    ones where it writes several steps. It is a pointer at something to
    look at, not a finding.
    """
    if len(values) != len(outcomes):
        raise ValueError("values and outcomes must be the same length")
    n = len(values)
    if n < 3:
        return None
    ys = [1.0 if o else 0.0 for o in outcomes]
    mean_x = sum(values) / n
    mean_y = sum(ys) / n
    sx = statistics.pstdev(values)
    sy = statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(values, ys, strict=True)) / n
    return covariance / (sx * sy)


@dataclass
class Correlation:
    """One metric's relationship with success."""

    metric: str
    r: float
    n: int
    mean_verified: float
    mean_failed: float

    @property
    def direction(self) -> str:
        return "higher in passes" if self.r > 0 else "higher in failures"

    @property
    def strength(self) -> str:
        magnitude = abs(self.r)
        if magnitude >= 0.5:
            return "strong"
        if magnitude >= 0.3:
            return "moderate"
        if magnitude >= 0.1:
            return "weak"
        return "negligible"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "r": round(self.r, 4),
            "n": self.n,
            "mean_verified": round(self.mean_verified, 3),
            "mean_failed": round(self.mean_failed, 3),
            "strength": self.strength,
        }


def correlate_with_success(
    entries: Sequence[tuple[Mapping[str, Any], bool]],
    *,
    fields: Sequence[str],
    min_abs_r: float = 0.1,
) -> list[Correlation]:
    """Rank numeric metrics by how strongly they track success.

    The point of collecting many metrics is to find which ones carry
    signal; this is that search, done once over a run. Metrics below
    ``min_abs_r`` are dropped so the table is short enough to read.
    """
    if not entries:
        return []
    names = list(fields)
    if not names:
        # Nothing to search. Which metrics exist is the caller's
        # knowledge, not this module's -- stats has no opinion about
        # proofs.
        return []
    out: list[Correlation] = []
    for name in names:
        pairs = [
            (float(raw[name]), verified)
            for raw, verified in entries
            if raw and isinstance(raw.get(name), (int, float))
        ]
        if len(pairs) < 3:
            continue
        values = [v for v, _ in pairs]
        outcomes = [o for _, o in pairs]
        r = point_biserial(values, outcomes)
        if r is None or abs(r) < min_abs_r:
            continue
        passed = [v for v, o in pairs if o]
        failed = [v for v, o in pairs if not o]
        out.append(
            Correlation(
                metric=name,
                r=r,
                n=len(pairs),
                mean_verified=sum(passed) / len(passed) if passed else 0.0,
                mean_failed=sum(failed) / len(failed) if failed else 0.0,
            )
        )
    return sorted(out, key=lambda c: -abs(c.r))


def format_correlations(correlations: Sequence[Correlation]) -> str:
    if not correlations:
        return "metric/outcome correlations: none above the reporting threshold"
    lines = [
        "metric correlation with success (association, NOT causation)",
        "  %-32s %7s %6s %10s %10s  %s"
        % ("metric", "r", "n", "mean(pass)", "mean(fail)", "strength"),
    ]
    for c in correlations:
        lines.append(
            "  %-32s %+7.3f %6d %10.2f %10.2f  %s"
            % (c.metric, c.r, c.n, c.mean_verified, c.mean_failed, c.strength)
        )
    lines.append(
        "  Read these as pointers, not findings: a metric can track success "
        "because it tracks task difficulty."
    )
    return "\n".join(lines)


