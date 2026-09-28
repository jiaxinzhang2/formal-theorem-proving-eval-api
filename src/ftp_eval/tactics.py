"""Extracting and counting the tactics a proof uses.

Useful for three things a pass rate cannot tell you:

* **What a model actually does.** A model that solves 40% of a benchmark
  with ``nlinarith`` alone is a different result from one that solves 40%
  with twenty tactics, even though the number is the same.
* **Which tactics carry the score.** Comparing the tactic mix of verified
  proofs against failed ones shows what is working.
* **Degenerate strategies.** A spike in ``decide`` or ``native_decide``
  usually means the benchmark has become a computation exercise rather
  than a proving one.

The extractor is lexical, not a parser: it finds tactic-position tokens
and counts them. It does not know whether a tactic ran, only that the
proof text contains it, so treat the counts as a description of what the
model wrote rather than of what the prover executed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .soundness import strip_comments

__all__ = [
    "extract_tactics",
    "TacticStats",
    "tactic_stats",
    "LEAN4_TACTICS",
]

#: Lean 4 / Mathlib tactics worth naming. Keeping an explicit vocabulary
#: rather than "first identifier on the line" avoids counting local
#: hypothesis names and term-mode identifiers as tactics.
LEAN4_TACTICS: frozenset[str] = frozenset(
    {
        # structural
        "intro", "intros", "exact", "apply", "refine", "constructor", "cases",
        "rcases", "obtain", "rintro", "use", "exists", "left", "right", "split",
        "split_ifs", "by_cases", "rfl", "subst", "revert", "clear", "rename_i",
        "induction", "cases'", "injection", "contradiction", "exfalso", "absurd",
        "have", "let", "set", "show", "suffices", "calc", "conv", "change",
        "specialize", "generalize", "with_reducible", "focus", "all_goals",
        "any_goals", "repeat", "first", "try", "skip", "done", "swap", "pick_goal",
        "rotate_left", "iterate", "guard_hyp", "case", "next", "on_goal",
        # rewriting / simplification
        "rw", "rwa", "simp", "simp_all", "simpa", "simp_arith", "simp_rw",
        "norm_num", "norm_cast", "push_cast", "ring", "ring_nf", "field_simp",
        "abel", "group", "unfold", "dsimp", "delta", "beta_reduce", "push_neg",
        "norm_fin", "gcongr", "mono", "congr", "ac_rfl",
        # decision procedures / automation
        "decide", "native_decide", "omega", "linarith", "nlinarith", "polyrith",
        "positivity", "tauto", "itauto", "aesop", "exact?", "apply?", "hint",
        "trivial", "assumption", "norm_bound", "bound", "fin_cases", "interval_cases",
        "measurability", "continuity", "fun_prop", "sorry", "admit",
        # bigger hammers
        "nlinarith!", "linear_combination", "nlinarith?", "slim_check", "plausible",
    }
)

#: Tactic names whose presence says something about soundness, tracked so
#: a report can surface them next to the frequency table.
_SUSPECT_TACTICS = frozenset({"sorry", "admit", "native_decide"})

#: Identifier tokens, excluding the tail of a qualified name. The
#: lookbehind is what keeps `Set.decide` from counting as the `decide`
#: tactic, and matching identifiers rather than consuming separators is
#: what keeps the *first* tactic of a proof -- the one right after `by`
#: -- from being swallowed by the separator match.
_LEAN_TOKEN_RE = re.compile(r"(?<![.\w'])(?P<name>[a-zA-Z_][a-zA-Z0-9_'!?]*)")

#: Coq tactics, for when a Coq backend is added.
_COQ_TACTICS: frozenset[str] = frozenset(
    {
        "intros", "intro", "apply", "exact", "auto", "eauto", "lia", "nia", "ring",
        "field", "omega", "reflexivity", "symmetry", "transitivity", "rewrite",
        "simpl", "unfold", "destruct", "induction", "inversion", "discriminate",
        "injection", "split", "left", "right", "exists", "assumption", "trivial",
        "contradiction", "congruence", "subst", "generalize", "specialize", "admit",
    }
)

_VOCABULARIES: dict[str, frozenset[str]] = {
    "lean4": LEAN4_TACTICS,
    "lean3": LEAN4_TACTICS,
    "coq": _COQ_TACTICS,
}


def extract_tactics(proof: str, language: str = "lean4") -> tuple[str, ...]:
    """Tactic names appearing in ``proof``, in order, with repeats kept.

    Comments are stripped first, so a tactic named in prose is not
    counted. Returns an empty tuple for a language with no vocabulary
    rather than guessing -- an empty result reads as "not measured", which
    is true, while a guessed one would quietly skew a frequency table.
    """
    vocabulary = _VOCABULARIES.get(language)
    if not vocabulary or not proof.strip():
        return ()
    body = strip_comments(proof, language)
    # Vocabulary membership does the filtering, so a hypothesis name or a
    # cited lemma is not mistaken for a tactic. A term-mode proof that
    # happens to use `rfl` as a term will count it, which is a known and
    # acceptable imprecision: it is doing the same job there.
    return tuple(
        match.group("name")
        for match in _LEAN_TOKEN_RE.finditer(body)
        if match.group("name") in vocabulary
    )


@dataclass
class TacticStats:
    """Tactic frequencies over a set of results, cut by outcome.

    The per-outcome split is the point: the same tactic appearing in both
    columns tells you nothing, while one that appears almost only under
    ``verified`` is doing the work.
    """

    #: tactic -> times it appears anywhere
    total: dict[str, int] = field(default_factory=dict)
    #: tactic -> number of *proofs* containing it at least once
    proofs_using: dict[str, int] = field(default_factory=dict)
    #: tactic -> number of verified proofs containing it
    in_verified: dict[str, int] = field(default_factory=dict)
    #: tactic -> number of scoreable-but-unverified proofs containing it
    in_failed: dict[str, int] = field(default_factory=dict)
    #: How many proofs contributed to these counts.
    proofs_measured: int = 0
    #: Proofs with no recognized tactic at all (term-mode, or empty).
    proofs_without_tactics: int = 0
    #: Distinct tactics per proof, averaged. A proxy for strategy variety.
    mean_distinct_per_proof: float = 0.0

    def success_rate(self, tactic: str) -> float | None:
        """Fraction of proofs using ``tactic`` that verified.

        ``None`` when the tactic never appears. This is an association,
        not a causal claim: ``omega`` looking good may only mean it gets
        pointed at arithmetic goals that were easy anyway.
        """
        used = self.in_verified.get(tactic, 0) + self.in_failed.get(tactic, 0)
        if not used:
            return None
        return self.in_verified.get(tactic, 0) / used

    def most_common(self, limit: int = 20) -> list[tuple[str, int]]:
        return sorted(self.total.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    @property
    def suspect_usage(self) -> dict[str, int]:
        """Counts for tactics that bear on soundness, if any appear."""
        return {t: self.proofs_using[t] for t in sorted(_SUSPECT_TACTICS) if t in self.proofs_using}

    def to_dict(self) -> dict[str, Any]:
        return {
            "proofs_measured": self.proofs_measured,
            "proofs_without_tactics": self.proofs_without_tactics,
            "mean_distinct_per_proof": round(self.mean_distinct_per_proof, 3),
            "total": self.total,
            "proofs_using": self.proofs_using,
            "in_verified": self.in_verified,
            "in_failed": self.in_failed,
            "suspect_usage": self.suspect_usage,
            "success_rate": {
                t: round(r, 4)
                for t, r in ((t, self.success_rate(t)) for t in self.proofs_using)
                if r is not None
            },
        }

    def format_text(self, limit: int = 20) -> str:
        if not self.proofs_measured:
            return "tactics: not measured"
        lines = [
            "tactic frequency (%d proof(s) measured, %d with no recognized tactic)"
            % (self.proofs_measured, self.proofs_without_tactics),
            "  %-18s %7s %7s %8s  %s" % ("tactic", "uses", "proofs", "verified", "solve%"),
        ]
        for tactic, count in self.most_common(limit):
            rate = self.success_rate(tactic)
            lines.append(
                "  %-18s %7d %7d %8d  %s"
                % (
                    tactic,
                    count,
                    self.proofs_using.get(tactic, 0),
                    self.in_verified.get(tactic, 0),
                    "   n/a" if rate is None else "%5.1f%%" % (100 * rate),
                )
            )
        remaining = len(self.total) - min(limit, len(self.total))
        if remaining > 0:
            lines.append("  ... and %d more distinct tactic(s)" % remaining)
        lines.append("  mean distinct tactics per proof: %.2f" % self.mean_distinct_per_proof)
        if self.suspect_usage:
            lines.append(
                "  NOTE soundness-relevant tactics present: "
                + ", ".join("%s in %d proof(s)" % (t, n) for t, n in self.suspect_usage.items())
            )
        return "\n".join(lines)


def tactic_stats(
    entries: Iterable[tuple[Sequence[str], bool, bool]],
) -> TacticStats:
    """Aggregate ``(tactics, verified, scoreable)`` triples.

    ``scoreable`` excludes harness errors from the per-outcome split, for
    the same reason the pass rate excludes them: an attempt that never got
    a verdict is not evidence about the tactics it used.
    """
    stats = TacticStats()
    total: Counter[str] = Counter()
    proofs_using: Counter[str] = Counter()
    in_verified: Counter[str] = Counter()
    in_failed: Counter[str] = Counter()
    distinct_counts: list[int] = []

    for tactics, verified, scoreable in entries:
        stats.proofs_measured += 1
        unique = set(tactics)
        if not unique:
            stats.proofs_without_tactics += 1
        distinct_counts.append(len(unique))
        total.update(tactics)
        proofs_using.update(unique)
        if scoreable:
            (in_verified if verified else in_failed).update(unique)

    stats.total = dict(total)
    stats.proofs_using = dict(proofs_using)
    stats.in_verified = dict(in_verified)
    stats.in_failed = dict(in_failed)
    stats.mean_distinct_per_proof = (
        sum(distinct_counts) / len(distinct_counts) if distinct_counts else 0.0
    )
    return stats


def stats_from_results(results: Iterable[Mapping[str, Any]]) -> TacticStats:
    """Build stats from serialized results (a ``results.jsonl`` stream)."""
    return tactic_stats(
        (
            tuple(r.get("tactics") or ()),
            r.get("status") == "verified",
            r.get("status") in ("verified", "rejected", "failed", "timeout"),
        )
        for r in results
    )
