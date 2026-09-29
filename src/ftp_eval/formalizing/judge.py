"""LLM judges for the question a prover cannot answer.

A prover can tell you a formal statement typechecks, is not trivially
closable, and is not vacuous. It cannot tell you the statement *means*
what the natural-language problem said -- that comparison is semantic,
and the only scalable way to make it is to ask a model.

Which makes the judge the weakest link in the pipeline, so the design
here is about not trusting it more than it deserves:

* **Abstention is a real answer.** :class:`~ftp_eval.types.JudgeLabel`
  has ``UNSURE``, and a judge that abstains is routed to a human rather
  than averaged into a score.
* **Rejections get re-checked.** A single low-effort pass that says
  "unfaithful" and is never revisited is how a judge silently deletes
  correct work from a benchmark. :class:`ConsensusJudge` re-examines its
  own rejections before letting one stand.
* **Disagreement is recorded, not smoothed.** Every sample's label is
  kept, so "3 of 5 said faithful" does not become an unqualified pass.
* **Tokens are measured; dollars are estimated.** Usage comes from the
  provider; the dollar figure comes from a local price table and is
  labelled an estimate everywhere it appears.
"""

from __future__ import annotations

import abc
import hashlib
from collections import Counter
from typing import Any, Mapping, Sequence

from ..types import JudgeLabel, JudgeUsage, JudgeVerdict, StatementTask

__all__ = [
    "Judge",
    "JudgeError",
    "MockJudge",
    "ConsensusJudge",
    "JudgeInfo",
    "build_judge_prompt",
    "DEFAULT_RUBRIC",
]


class JudgeError(RuntimeError):
    """A judge could not produce a verdict. Never a silent ``UNFAITHFUL``."""


#: The criteria a judge is asked to apply. Kept as one frozen string so it
#: can be cached as a stable prompt prefix across a whole run -- on a
#: 500-task evaluation the rubric is re-sent 500 times otherwise.
DEFAULT_RUBRIC = """\
You are checking whether a formal theorem statement faithfully expresses a \
mathematical problem written in natural language. You are NOT checking \
whether the statement is provable, and you are NOT being asked to prove it.

Judge FAITHFUL only if all of these hold:
1. The conclusion states what the problem asks to show, neither weaker nor \
stronger.
2. Every hypothesis the problem gives is present, and no hypothesis has been \
added that the problem does not give. An added hypothesis that narrows the \
claim is unfaithful even if the narrowed claim is true.
3. Quantifiers match: a claim about all n is not the same as a claim about \
one particular n.
4. Types and domains match the problem (naturals vs integers vs reals, \
strict vs non-strict inequalities, open vs closed intervals).
5. If the problem asks for a specific value, the statement pins that value \
down rather than asserting mere existence.

Judge UNFAITHFUL if any of the above fails, or if the statement is about a \
different mathematical object than the problem.

Judge UNSURE if you genuinely cannot tell -- the problem is ambiguous, the \
notation is undefined, or deciding would need a definition you were not \
given. UNSURE is a useful answer and is preferred over a confident guess; \
it routes the item to a human instead of corrupting the score.

Be specific in your reasoning: name the mismatch, do not summarise the \
statement back."""


class JudgeInfo:
    """Self-description of a judge, for ``ftp-eval judges``."""

    def __init__(
        self,
        name: str,
        available: bool,
        *,
        model: str | None = None,
        detail: str | None = None,
        costs_money: bool = False,
    ) -> None:
        self.name = name
        self.available = available
        self.model = model
        self.detail = detail
        self.costs_money = costs_money

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "model": self.model,
            "detail": self.detail,
            "costs_money": self.costs_money,
        }


def build_judge_prompt(task: StatementTask, *, include_gold: bool = False) -> str:
    """The per-task half of the prompt.

    Deliberately separate from the rubric so the rubric can be a cached,
    byte-stable prefix and only this part varies per request.
    """
    lines = [
        "Natural-language problem:",
        task.informal_statement.strip() or "(none supplied)",
        "",
        "Candidate formal statement (%s):" % task.language,
    ]
    if task.header.strip():
        lines.append(task.header.strip())
    lines.append(task.formal_statement.strip())
    if include_gold and task.gold_formal_statement:
        lines += [
            "",
            "A reference formalization is also available. Treat it as one "
            "correct rendering, not the only one: a candidate that differs "
            "from it but still says what the problem says is FAITHFUL.",
            task.gold_formal_statement.strip(),
        ]
    return "\n".join(lines)


class Judge(abc.ABC):
    """Base class for faithfulness judges."""

    name: str = "unnamed"
    #: True when calling this judge spends money, so the CLI can say so.
    costs_money: bool = False

    def __init__(self, *, rubric: str = DEFAULT_RUBRIC, **config: Any) -> None:
        self.rubric = rubric
        self.config: Mapping[str, Any] = dict(config)

    @abc.abstractmethod
    def judge(self, task: StatementTask) -> JudgeVerdict:
        """Assess whether ``task.formal_statement`` matches its prose.

        Raise :class:`JudgeError` when no verdict could be obtained. Do
        not return ``UNFAITHFUL`` to signal a failure of the judge itself
        -- that silently turns an outage into a wave of rejections.
        """

    def info(self) -> JudgeInfo:
        return JudgeInfo(self.name, True, costs_money=self.costs_money)

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release any client or session. Idempotent."""

    def __enter__(self) -> "Judge":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return "<%s name=%r>" % (type(self).__name__, self.name)


class MockJudge(Judge):
    """Deterministic judge for tests, CI and dry runs. Spends nothing.

    Verdicts come from markers in the formal statement
    (``JUDGE_FAITHFUL`` / ``JUDGE_UNFAITHFUL`` / ``JUDGE_UNSURE`` /
    ``JUDGE_ERROR``), or from a stable hash of the statement when
    ``faithful_rate`` is set. It reports plausible token counts so that
    usage plumbing is exercised, with ``estimated_cost_usd`` at 0.0 --
    those tokens were never really spent.
    """

    name = "mock"
    costs_money = False

    def __init__(
        self,
        *,
        faithful_rate: float | None = None,
        seed: str = "ftp-eval",
        **config: Any,
    ) -> None:
        super().__init__(**config)
        if faithful_rate is not None and not 0.0 <= faithful_rate <= 1.0:
            raise ValueError("faithful_rate must be in [0, 1]")
        self.faithful_rate = faithful_rate
        self.seed = seed

    def info(self) -> JudgeInfo:
        return JudgeInfo(
            self.name,
            True,
            model="mock",
            detail="deterministic fake judge; no semantic assessment happens",
        )

    def judge(self, task: StatementTask) -> JudgeVerdict:
        text = task.formal_statement
        usage = JudgeUsage(
            input_tokens=len(self.rubric) // 4,
            output_tokens=24,
            calls=1,
            estimated_cost_usd=0.0,
            model="mock",
        )
        # A metadata field is the preferred way for a fixture to drive
        # this, since embedding a marker in the statement text would mean
        # putting non-Lean into a field that has to stay compilable.
        declared = str(task.metadata.get("mock_judge") or "").strip().lower()
        if declared == "error":
            raise JudgeError("mock judge asked to fail via metadata")
        if declared in ("faithful", "unfaithful", "unsure"):
            label = JudgeLabel(declared)
            return JudgeVerdict(
                label=label,
                reasoning="mock verdict from metadata mock_judge=%s" % declared,
                confidence=1.0,
                samples=(label,),
                usage=usage,
                raw=({"mock_judge": declared},),
            )

        if "JUDGE_ERROR" in text:
            raise JudgeError("mock judge asked to fail via JUDGE_ERROR")
        for marker, label in (
            ("JUDGE_UNFAITHFUL", JudgeLabel.UNFAITHFUL),
            ("JUDGE_UNSURE", JudgeLabel.UNSURE),
            ("JUDGE_FAITHFUL", JudgeLabel.FAITHFUL),
        ):
            if marker in text:
                return JudgeVerdict(
                    label=label,
                    reasoning="mock verdict from marker %s" % marker,
                    confidence=1.0,
                    samples=(label,),
                    usage=usage,
                    raw=({"marker": marker},),
                )
        if self.faithful_rate is not None:
            digest = hashlib.sha256((self.seed + text).encode("utf-8")).digest()
            draw = int.from_bytes(digest[:8], "big") / float(1 << 64)
            label = JudgeLabel.FAITHFUL if draw < self.faithful_rate else JudgeLabel.UNFAITHFUL
            return JudgeVerdict(
                label=label,
                reasoning="mock: pseudo-random draw %.4f" % draw,
                samples=(label,),
                usage=usage,
                raw=({"draw": round(draw, 6)},),
            )
        return JudgeVerdict(
            label=JudgeLabel.UNSURE,
            reasoning="mock: no JUDGE_* marker and no faithful_rate, so abstaining",
            samples=(JudgeLabel.UNSURE,),
            usage=usage,
        )


class ConsensusJudge(Judge):
    """Runs another judge several times and aggregates honestly.

    Two behaviours, both there because of how single-pass judges fail:

    **Majority vote with a tie going to UNSURE.** Sampling the same judge
    n times and taking the mode is a cheap variance reduction. A split
    decision is reported as ``UNSURE`` rather than broken arbitrarily,
    because a coin flip recorded as a verdict is indistinguishable from a
    real one downstream.

    **Rejections are re-examined.** When the first pass says
    ``UNFAITHFUL``, ``recheck_rejections`` draws additional samples before
    that stands. A judge that rejects once and never looks again quietly
    deletes correct formalizations from a benchmark, and a false rejection
    is far more expensive than a false acceptance here: the rejected item
    disappears from the results, while a wrongly accepted one is still
    visible and auditable.
    """

    name = "consensus"

    def __init__(
        self,
        inner: Judge,
        *,
        samples: int = 3,
        recheck_rejections: int = 2,
        **config: Any,
    ) -> None:
        super().__init__(**config)
        if samples < 1:
            raise ValueError("samples must be >= 1")
        self.inner = inner
        self.samples = samples
        self.recheck_rejections = max(0, recheck_rejections)
        self.costs_money = inner.costs_money
        self.name = "consensus(%s)" % inner.name

    def info(self) -> JudgeInfo:
        inner = self.inner.info()
        return JudgeInfo(
            self.name,
            inner.available,
            model=inner.model,
            detail="%d sample(s), %d extra on a rejection%s"
            % (
                self.samples,
                self.recheck_rejections,
                "" if inner.detail is None else "; %s" % inner.detail,
            ),
            costs_money=inner.costs_money,
        )

    def judge(self, task: StatementTask) -> JudgeVerdict:
        verdicts = [self.inner.judge(task) for _ in range(self.samples)]
        labels = [v.label for v in verdicts]

        if any(label is JudgeLabel.UNFAITHFUL for label in labels) and self.recheck_rejections:
            verdicts += [self.inner.judge(task) for _ in range(self.recheck_rejections)]
            labels = [v.label for v in verdicts]
            aggregation_note = "rechecked"
        else:
            aggregation_note = "vote"

        counts = Counter(labels)
        top = counts.most_common()
        if len(top) > 1 and top[0][1] == top[1][1]:
            label, aggregation = JudgeLabel.UNSURE, "%s: tie %s -> unsure" % (
                aggregation_note,
                dict(counts),
            )
        else:
            label = top[0][0]
            aggregation = "%s: %d/%d %s" % (aggregation_note, top[0][1], len(labels), label.value)

        usage = JudgeUsage()
        for v in verdicts:
            usage = usage.merge(v.usage)

        # Prefer the reasoning of a sample that agrees with the outcome;
        # quoting a dissenting explanation next to the final label is how
        # a report becomes impossible to trust.
        agreeing = [v for v in verdicts if v.label is label]
        reasoning = (agreeing[0] if agreeing else verdicts[0]).reasoning
        confidences = [v.confidence for v in agreeing if v.confidence is not None]

        return JudgeVerdict(
            label=label,
            reasoning=reasoning,
            confidence=sum(confidences) / len(confidences) if confidences else None,
            samples=tuple(labels),
            aggregation=aggregation,
            usage=usage,
            raw=tuple(r for v in verdicts for r in v.raw),
        )

    def close(self) -> None:
        self.inner.close()


def aggregate_usage(verdicts: Sequence[JudgeVerdict | None]) -> JudgeUsage:
    """Total usage across a run, for the cost line in a report."""
    total = JudgeUsage()
    for v in verdicts:
        if v is not None:
            total = total.merge(v.usage)
    return total
