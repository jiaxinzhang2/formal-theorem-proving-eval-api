"""Faithfulness judge backed by the Claude API.

Three choices worth knowing about before you run this on a paid key:

**Structured output, not parsing.** The verdict comes back through
``output_config.format`` with a closed JSON schema, so the label is one of
three enum values by construction. Regex-scraping "FAITHFUL" out of prose
is how a judge's abstention silently becomes a rejection.

**The rubric is a cached prefix.** It is identical on every request, so it
is sent as a ``cache_control`` block and read from cache after the first
call. On a 500-statement run that is the difference between paying for the
rubric once and paying for it 500 times.

**Tokens measured, dollars estimated.** ``usage`` is copied from the
API response. The dollar figure is computed here from a price table with
an explicit ``as of`` date and is reported as an estimate, because a local
price table drifts and the provider's invoice is the only authority. Check
the real number against your billing console before quoting it.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from ..judge import Judge, JudgeError, JudgeInfo, build_judge_prompt
from ...backends.types import StatementTask
from ..types import JudgeLabel, JudgeUsage, JudgeVerdict

__all__ = ["ClaudeJudge", "PRICES_USD_PER_MTOK", "PRICES_AS_OF"]

#: USD per million tokens, (input, output). A local table, kept explicit
#: so it is obvious when it goes stale rather than quietly wrong.
PRICES_AS_OF = "2026-06-24"
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5-1": (10.00, 50.00),
}

#: Closed schema: the label cannot come back as anything but these three.
_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "enum": ["faithful", "unfaithful", "unsure"],
            "description": "Your verdict on whether the formal statement matches the problem.",
        },
        "reasoning": {
            "type": "string",
            "description": (
                "Name the specific mismatch, or state why the two agree. "
                "Do not restate the formal statement."
            ),
        },
        "confidence": {
            "type": "number",
            "description": "How confident you are in this verdict, from 0 to 1.",
        },
    },
    "required": ["label", "reasoning", "confidence"],
    "additionalProperties": False,
}


def estimate_cost_usd(model: str, usage: Mapping[str, Any]) -> float | None:
    """Estimate one call's cost. ``None`` when the model is not in the table.

    Cache reads are billed at roughly a tenth of the input rate and cache
    writes at about 1.25x; both are applied here. Returning ``None`` for an
    unknown model is deliberate -- a missing price should read as "unknown",
    never as free.
    """
    price = PRICES_USD_PER_MTOK.get(model)
    if price is None:
        return None
    input_rate, output_rate = price
    per_token_in = input_rate / 1_000_000
    per_token_out = output_rate / 1_000_000
    return (
        int(usage.get("input_tokens") or 0) * per_token_in
        + int(usage.get("output_tokens") or 0) * per_token_out
        + int(usage.get("cache_read_input_tokens") or 0) * per_token_in * 0.1
        + int(usage.get("cache_creation_input_tokens") or 0) * per_token_in * 1.25
    )


class ClaudeJudge(Judge):
    """Judge formalization faithfulness with Claude.

    Parameters
    ----------
    model:
        Defaults to ``claude-opus-5``. Judging a formalization is a
        reasoning task where a wrong verdict silently corrupts a
        benchmark, so the capable model is the default; pass
        ``model="claude-sonnet-5"`` to trade accuracy for cost knowingly.
    effort:
        ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max``. Defaults to
        ``high``.
    cache_rubric:
        Send the rubric as a cached prefix (default true).
    """

    name = "claude"
    costs_money = True

    def __init__(
        self,
        *,
        model: str = "claude-opus-5",
        effort: str = "high",
        max_tokens: int = 4096,
        cache_rubric: bool = True,
        include_gold: bool = False,
        api_key: str | None = None,
        client: Any = None,
        **config: Any,
    ) -> None:
        super().__init__(**config)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.cache_rubric = cache_rubric
        self.include_gold = include_gold
        self._api_key = api_key
        self._client = client

    # -- availability -------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:
            raise JudgeError(
                "the claude judge needs the anthropic SDK: pip install "
                "'formal-theorem-proving-eval-api[judge]'"
            ) from exc
        # A bare constructor also picks up an `ant auth login` profile, so
        # an unset ANTHROPIC_API_KEY does not mean there are no credentials.
        self._client = (
            anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
        )
        return self._client

    def info(self) -> JudgeInfo:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return JudgeInfo(
                self.name,
                False,
                model=self.model,
                detail="anthropic SDK not installed: pip install "
                "'formal-theorem-proving-eval-api[judge]'",
                costs_money=True,
            )
        has_credential = bool(
            self._api_key
            or self._client
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        detail = "model %s, effort %s; cost figures are ESTIMATES from a price table as of %s" % (
            self.model,
            self.effort,
            PRICES_AS_OF,
        )
        if not has_credential:
            detail = (
                "no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the environment. "
                "If you have run `ant auth login`, the SDK will use that profile "
                "and this will work anyway; otherwise set a key. " + detail
            )
        return JudgeInfo(self.name, True, model=self.model, detail=detail, costs_money=True)

    # -- judging ------------------------------------------------------

    def judge(self, task: StatementTask) -> JudgeVerdict:
        client = self._get_client()
        system: list[dict[str, Any]] = [{"type": "text", "text": self.rubric}]
        if self.cache_rubric:
            # Byte-identical across every call in a run, so it caches.
            system[0]["cache_control"] = {"type": "ephemeral"}

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": _VERDICT_SCHEMA},
                },
                messages=[
                    {
                        "role": "user",
                        "content": build_judge_prompt(task, include_gold=self.include_gold),
                    }
                ],
            )
        except Exception as exc:
            # Any API failure is a JudgeError, never an UNFAITHFUL verdict:
            # an outage must not turn into a wave of rejections.
            raise JudgeError("claude judge call failed: %s: %s" % (type(exc).__name__, exc)) from exc

        if getattr(response, "stop_reason", None) == "refusal":
            raise JudgeError(
                "claude declined to judge this item (stop_reason=refusal); it is "
                "reported as unjudged rather than unfaithful"
            )

        payload = self._parse(response)
        usage = self._usage(response)
        label = JudgeLabel(payload["label"])
        confidence = payload.get("confidence")
        return JudgeVerdict(
            label=label,
            reasoning=str(payload.get("reasoning") or ""),
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            samples=(label,),
            usage=usage,
            raw=(
                {
                    "model": getattr(response, "model", self.model),
                    "stop_reason": getattr(response, "stop_reason", None),
                    "verdict": payload,
                },
            ),
        )

    def _parse(self, response: Any) -> dict[str, Any]:
        text = next(
            (b.text for b in getattr(response, "content", []) if getattr(b, "type", None) == "text"),
            None,
        )
        if not text:
            raise JudgeError("claude returned no text block to parse a verdict from")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JudgeError("claude returned non-JSON despite a schema: %s" % text[:300]) from exc
        if not isinstance(payload, dict) or payload.get("label") not in (
            "faithful",
            "unfaithful",
            "unsure",
        ):
            raise JudgeError("claude returned an unusable verdict: %s" % text[:300])
        return payload

    def _usage(self, response: Any) -> JudgeUsage:
        raw = getattr(response, "usage", None)
        if raw is None:
            return JudgeUsage(calls=1, model=self.model)
        fields = {
            name: int(getattr(raw, name, 0) or 0)
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        }
        model = getattr(response, "model", self.model) or self.model
        return JudgeUsage(
            calls=1,
            model=model,
            estimated_cost_usd=estimate_cost_usd(model, fields),
            **fields,
        )
