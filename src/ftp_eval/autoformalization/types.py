"""What the autoformalization API concludes, and what a judge reported.

None of this is visible to ``proving/``: a proof check never produces a
``Check`` or a ``JudgeVerdict``. The vocabulary the two APIs do share --
what a prover is asked and what it answers -- is in ``backends/types.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


__all__ = [
    "CheckKind",
    "Check",
    "StatementStatus",
    "StatementVerdict",
    "JudgeLabel",
    "JudgeUsage",
    "JudgeVerdict",
]


class CheckKind(str, Enum):
    """What kind of evidence a :class:`Check` carries.

    One kind, because this API asks one question. Everything that used to
    be here -- elaborates, non_trivial, non_vacuous, gold_equivalent,
    no_placeholder -- asked about the Lean rather than about the prose, and
    lives in :mod:`ftp_eval.proving.grading.problem_health` as
    :class:`HealthKind`.

    Kept as an enum rather than dropped so the verdict's JSON shape stays
    stable and a second kind of faithfulness evidence (a second judge, a
    human review) has somewhere to go.
    """

    JUDGE_FAITHFUL = "judge_faithful"


@dataclass(frozen=True)
class Check:
    """The outcome of one statement check.

    ``passed is None`` means the check did not run or could not decide.
    That is distinct from ``False`` and is never silently read as a pass.
    """

    kind: CheckKind
    passed: bool | None
    detail: str = ""
    #: True when failing this check proves the formalization is broken,
    #: rather than merely suspicious.
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "passed": self.passed,
            "detail": self.detail,
            "fatal": self.fatal,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Check":
        return cls(
            kind=CheckKind(d["kind"]),
            passed=d.get("passed"),
            detail=d.get("detail") or "",
            fatal=bool(d.get("fatal", False)),
        )


class StatementStatus(str, Enum):
    """Verdict on a formalization."""

    #: A judge read the formalization as faithful to its prose.
    OK = "ok"
    #: A judge read it as *not* matching the problem. A definite negative,
    #: not a suspicion -- which is why it is not called "suspicious" any
    #: more. Whether the Lean is well formed is a separate question, asked
    #: by :mod:`ftp_eval.proving.grading.problem_health`.
    UNFAITHFUL = "unfaithful"
    #: Nothing was established: no judge configured, no prose to judge
    #: against, the judge abstained, or the API failed. Never a pass.
    INCONCLUSIVE = "inconclusive"
    #: The harness broke.
    ERROR = "error"

    @property
    def usable_as_ground_truth(self) -> bool:
        """Whether a proof against this statement means anything.

        ``INCONCLUSIVE`` is deliberately included: not having checked is
        not the same as having found a problem, and refusing to score
        every unjudged task would make the tool unusable without an API
        key. What it is not is evidence of faithfulness, which is why
        :class:`StatementVerdict` keeps ``checked_faithfulness`` separate.
        """
        return self in (StatementStatus.OK, StatementStatus.INCONCLUSIVE)


@dataclass(frozen=True)
class StatementVerdict:
    """Everything concluded about one formalization."""

    task_id: str
    status: StatementStatus
    checks: tuple[Check, ...] = ()
    judge: JudgeVerdict | None = None
    backend: str | None = None
    wall_time_s: float = 0.0
    split: str | None = None
    #: Untouched probe payloads, for auditing a surprising verdict.
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.passed is False)

    @property
    def checked_faithfulness(self) -> bool:
        """Whether a judge actually returned a verdict.

        False when no judge was configured, when the problem records no
        prose to judge against, when the judge abstained, and when its API
        failed. All four are "we do not know", and a report has to
        distinguish that from "we checked and it is fine" -- which is the
        whole reason this property exists rather than reading the status.
        """
        return any(
            c.kind is CheckKind.JUDGE_FAITHFUL and c.passed is not None for c in self.checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "checks": [c.to_dict() for c in self.checks],
            "judge": self.judge.to_dict() if self.judge else None,
            "backend": self.backend,
            "wall_time_s": round(self.wall_time_s, 4),
            "split": self.split,
            "checked_faithfulness": self.checked_faithfulness,
            "raw": dict(self.raw),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StatementVerdict":
        return cls(
            task_id=d["task_id"],
            status=StatementStatus(d["status"]),
            checks=tuple(Check.from_dict(c) for c in d.get("checks") or ()),
            judge=JudgeVerdict.from_dict(d["judge"]) if d.get("judge") else None,
            backend=d.get("backend"),
            wall_time_s=float(d.get("wall_time_s") or 0.0),
            split=d.get("split"),
            raw=dict(d.get("raw") or {}),
        )


class JudgeLabel(str, Enum):
    """What an LLM judge concluded about a formalization.

    ``UNSURE`` is a first-class answer, not a failure. A judge forced to
    choose between faithful and unfaithful on a statement it cannot read
    produces confident noise, and confident noise is worse than an
    abstention you can route to a human.
    """

    FAITHFUL = "faithful"
    UNFAITHFUL = "unfaithful"
    UNSURE = "unsure"


@dataclass(frozen=True)
class JudgeUsage:
    """What a judge call consumed.

    Token counts are **measured** -- they come from the provider's own
    usage report. The dollar figure is an **estimate** computed from a
    local price table, and is labelled that way everywhere it surfaces,
    because a hardcoded price table drifts from real billing and a
    self-computed cost has no authority next to the provider's invoice.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0
    estimated_cost_usd: float | None = None
    model: str | None = None

    def merge(self, other: "JudgeUsage") -> "JudgeUsage":
        costs = [c for c in (self.estimated_cost_usd, other.estimated_cost_usd) if c is not None]
        return JudgeUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            calls=self.calls + other.calls,
            estimated_cost_usd=sum(costs) if costs else None,
            model=self.model or other.model,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "calls": self.calls,
            "estimated_cost_usd": self.estimated_cost_usd,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JudgeUsage":
        return cls(
            input_tokens=int(d.get("input_tokens") or 0),
            output_tokens=int(d.get("output_tokens") or 0),
            cache_read_input_tokens=int(d.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(d.get("cache_creation_input_tokens") or 0),
            calls=int(d.get("calls") or 0),
            estimated_cost_usd=d.get("estimated_cost_usd"),
            model=d.get("model"),
        )


@dataclass(frozen=True)
class JudgeVerdict:
    """An LLM judge's answer, with everything needed to audit it."""

    label: JudgeLabel
    reasoning: str = ""
    #: The judge's own stated confidence in [0, 1], when it reports one.
    confidence: float | None = None
    #: Individual labels when several samples were drawn, in order.
    samples: tuple[JudgeLabel, ...] = ()
    #: How the final label was reached ("single", "majority", "tie -> unsure").
    aggregation: str = "single"
    usage: JudgeUsage = field(default_factory=JudgeUsage)
    #: Raw provider payloads, kept so a surprising verdict can be read back.
    raw: tuple[Mapping[str, Any], ...] = ()

    @property
    def is_faithful(self) -> bool:
        return self.label is JudgeLabel.FAITHFUL

    @property
    def abstained(self) -> bool:
        return self.label is JudgeLabel.UNSURE

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label.value,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "samples": [s.value for s in self.samples],
            "aggregation": self.aggregation,
            "usage": self.usage.to_dict(),
            "raw": [dict(r) for r in self.raw],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "JudgeVerdict":
        return cls(
            label=JudgeLabel(d["label"]),
            reasoning=d.get("reasoning") or "",
            confidence=d.get("confidence"),
            samples=tuple(JudgeLabel(s) for s in d.get("samples") or ()),
            aggregation=d.get("aggregation") or "single",
            usage=JudgeUsage.from_dict(d.get("usage") or {}),
            raw=tuple(dict(r) for r in d.get("raw") or ()),
        )
