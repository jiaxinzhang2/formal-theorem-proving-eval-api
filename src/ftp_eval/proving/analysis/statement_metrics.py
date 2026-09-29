"""How hard a formal statement *looks*, before anyone tries to prove it.

Belongs to the autoformalization side: these measure the statement a
formalizer produced, not the proof someone wrote for it. Correlating solve
rate against them is the closest thing to a difficulty axis available
without human labels.

Surface complexity, not mathematical depth -- a short statement can be an
open problem. A proxy, never a difficulty score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ...backends.comments import strip_comments

__all__ = ["StatementComplexity", "analyze_statement", "STATEMENT_METRIC_FIELDS"]

#: Every token: identifiers, numbers, single punctuation characters.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'!?.]*|\d+|[^\sA-Za-z0-9_]")

#: A qualified name such as `Nat.Prime` -- almost always a cited definition.
_QUALIFIED_RE = re.compile(r"\b([A-Z][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_'!?]*)+)")

_OPENERS = "([{⟨⦃"
_CLOSERS = ")]}⟩⦄"


def _max_bracket_depth(text: str) -> int:
    depth = best = 0
    for ch in text:
        if ch in _OPENERS:
            depth += 1
            best = max(best, depth)
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
    return best


#: Recorded on every result as ``statement_*``.
STATEMENT_METRIC_FIELDS = (
    "binders",
    "hypotheses",
    "conclusion_tokens",
    "quantifiers",
    "connectives",
    "cited_definitions",
    "distinct_types",
    "total_tokens",
    "max_nesting_depth",
)


@dataclass
class StatementComplexity:
    """How hard a formal statement *looks*, before anyone tries it.

    The point is correlation: bucketing solve rate by these is the closest
    thing to a difficulty axis that can be computed without human labels.
    They measure the statement's surface, not its mathematical depth -- a
    short statement can be an open problem -- so treat them as a proxy,
    not a difficulty score.
    """

    binders: int = 0
    #: Binders whose type is a Prop-looking claim, i.e. hypotheses.
    hypotheses: int = 0
    implicit_binders: int = 0
    instance_binders: int = 0
    conclusion_tokens: int = 0
    quantifiers: int = 0
    connectives: int = 0
    #: Distinct qualified names the statement mentions (`Nat.Prime`, ...).
    cited_definitions: int = 0
    #: Distinct types named in binders, a proxy for how many domains are
    #: in play at once.
    distinct_types: int = 0
    total_tokens: int = 0
    max_nesting_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "binders": self.binders,
            "hypotheses": self.hypotheses,
            "implicit_binders": self.implicit_binders,
            "instance_binders": self.instance_binders,
            "conclusion_tokens": self.conclusion_tokens,
            "quantifiers": self.quantifiers,
            "connectives": self.connectives,
            "cited_definitions": self.cited_definitions,
            "distinct_types": self.distinct_types,
            "total_tokens": self.total_tokens,
            "max_nesting_depth": self.max_nesting_depth,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StatementComplexity":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


#: Named binders: `(x : α)`, `{α : Type}`, `⦃i : ι⦄`.
_BINDER_RE = re.compile(r"([({⦃])\s*([^:()\[\]{}⦃⦄]*?)\s*:\s*([^()\[\]{}⦃⦄]*)\s*([)}⦄])")

#: Instance binders, which in Lean 4 are usually *anonymous* -- `[Ring α]`
#: rather than `[inst : Ring α]` -- so they cannot be found by a pattern
#: that requires a colon.
_INSTANCE_BINDER_RE = re.compile(r"\[\s*([^()\[\]{}⦃⦄]+?)\s*\]")
_QUANTIFIER_RE = re.compile(r"∀|∃|\\forall|\\exists|\bforall\b|\bexists\b")
_CONNECTIVE_RE = re.compile(r"∧|∨|→|↔|¬|\\land|\\lor|->|<->|\bAnd\b|\bOr\b|\bIff\b|\bNot\b")
#: A binder type that looks like a proposition rather than a carrier set:
#: it contains a relation or a connective.
_PROP_TYPE_RE = re.compile(r"[=<>≤≥≠∈∉⊆∣]|∧|∨|→|↔|¬|\bPrime\b|\bOdd\b|\bEven\b")


def analyze_statement(
    formal_statement: str, language: str = "lean4"
) -> StatementComplexity:
    """Measure a formal statement's surface complexity.

    Deliberately self-contained rather than reusing the Lean backend's
    parser: this must work when no prover is configured, and a statement
    that does not parse should still yield token and quantifier counts
    instead of nothing.
    """
    complexity = StatementComplexity()
    if not formal_statement.strip():
        return complexity

    body = strip_comments(formal_statement, language)
    # Cut the proof seam so a `:= by ...` tail is not counted as content.
    head = re.split(r":=", body, maxsplit=1)[0]

    complexity.total_tokens = len(_TOKEN_RE.findall(head))
    complexity.quantifiers = len(_QUANTIFIER_RE.findall(head))
    complexity.connectives = len(_CONNECTIVE_RE.findall(head))
    complexity.cited_definitions = len(set(_QUALIFIED_RE.findall(head)))
    complexity.max_nesting_depth = _max_bracket_depth(head)

    types: set[str] = set()
    for opener, _names, type_text, _closer in _BINDER_RE.findall(head):
        complexity.binders += 1
        if opener in ("{", "⦃"):
            complexity.implicit_binders += 1
        cleaned = type_text.strip()
        if cleaned:
            types.add(re.sub(r"\s+", " ", cleaned))
        if _PROP_TYPE_RE.search(cleaned):
            complexity.hypotheses += 1

    for instance_type in _INSTANCE_BINDER_RE.findall(head):
        complexity.binders += 1
        complexity.instance_binders += 1
        # `[inst : Ring α]` is legal too; keep only the type half.
        cleaned = instance_type.split(":", 1)[-1].strip()
        if cleaned:
            types.add(re.sub(r"\s+", " ", cleaned))
    complexity.distinct_types = len(types)

    # The conclusion is whatever follows the last binder of either kind.
    last_end = 0
    for pattern in (_BINDER_RE, _INSTANCE_BINDER_RE):
        for match in pattern.finditer(head):
            last_end = max(last_end, match.end())
    conclusion = head[last_end:] if last_end else head
    conclusion = re.sub(r"^\s*:\s*", "", conclusion.strip())
    complexity.conclusion_tokens = len(_TOKEN_RE.findall(conclusion))
    return complexity


# ---------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------


