"""Structural metrics for a proof: size, shape, dependencies, comments.

A pass rate says whether a model solved a problem. These say *what it
wrote*, which is what you need to compare two models that score the same,
or to notice that a benchmark has drifted into rewarding one-liners.

Everything here is lexical. That is a real limit and it is worth being
precise about where it bites:

* **Declaration and comment counts are exact** -- they are just counting.
* **Nesting and branch counts are structural** and reliable for
  well-formatted Lean, approximate for unusual layout.
* **Dependency depth is local.** It is the longest chain through the
  proof's *own* intermediate steps (``have``/``set``/auxiliary lemmas),
  which is computable from the text. The *global* depth -- how deep the
  cited Mathlib lemmas go -- needs the prover's environment and is not
  computed here; a field that claimed to know it would be lying.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ...shared.comments import strip_comments
from ...shared.stats import Distribution, distribution
from .tactics import extract_tactics

__all__ = [
    "ProofStructure",
    "analyze_proof",
    "NUMERIC_FIELDS",
    "StructureStats",
    "aggregate_structure",
    "SampleDuplication",
    "sample_duplication",
]

_DECLARATION_KINDS = (
    "theorem", "lemma", "example", "def", "abbrev", "instance", "structure",
    "inductive", "class", "axiom", "opaque", "constant",
)

_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|local\s+|scoped\s+|noncomputable\s+|"
    r"unsafe\s+|partial\s+|nonrec\s+)*(%s)\b" % "|".join(_DECLARATION_KINDS),
    re.MULTILINE,
)

#: Tactics that introduce a *named* intermediate result, which is what a
#: local dependency chain is made of.
_STEP_RE = re.compile(
    r"\b(?:have|let|set|obtain|suffices)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_'!?]*)\s*(?::|:=|with)",
)

#: Tactics that split the goal, i.e. branch points. The rough analogue of
#: cyclomatic complexity for a proof.
_BRANCH_RE = re.compile(
    r"\b(?:cases|rcases|obtain|by_cases|split|split_ifs|induction|interval_cases|"
    r"fin_cases|constructor|refine|rintro|match)\b|<;>|·|\|"
)

#: A qualified name such as `Nat.succ_le_of_lt` -- almost always a cited
#: library lemma rather than something local.
_QUALIFIED_RE = re.compile(r"\b([A-Z][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_'!?]*)+)")

#: Plain identifiers, used to find which earlier steps a justification
#: cites without a per-name regex search.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'!?]*")

#: Tactics that consume a named result to finish the proof.
_CLOSING_TACTIC_RE = re.compile(r"\b(?:exact|exact\?|apply|linarith|nlinarith|simpa|omega)\b")

#: One tokenization shared by the size and repetition metrics.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'!?.]*|\d+|[^\sA-Za-z0-9_]")

_BLOCK_COMMENT_RE = re.compile(r"/-(?:.|\n)*?-/")
_DOC_COMMENT_RE = re.compile(r"/--(?:.|\n)*?-/")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

_OPENERS = "([{⟨⦃"
_CLOSERS = ")]}⟩⦄"


@dataclass
class ProofStructure:
    """Structural description of one proof.

    Field groups: size, declarations, steps and dependencies, control
    flow, comments. ``to_dict`` is what lands in the results file.
    """

    # -- size ----------------------------------------------------------
    chars: int = 0
    lines: int = 0
    code_lines: int = 0
    tokens: int = 0
    #: Longest line, a cheap signal for generated-looking one-liners.
    max_line_chars: int = 0

    # -- declarations --------------------------------------------------
    declarations: dict[str, int] = field(default_factory=dict)
    #: Declarations beyond the theorem under test: helper lemmas the model
    #: wrote for itself. A high count is a real proof-engineering signal.
    auxiliary_declarations: int = 0

    # -- steps and dependencies ---------------------------------------
    named_steps: int = 0
    #: Longest chain through the proof's own named steps. See the module
    #: docstring: local, not global.
    local_dependency_depth: int = 0
    #: Distinct qualified names cited, a proxy for library reliance.
    cited_lemmas: int = 0
    calc_blocks: int = 0

    # -- control flow --------------------------------------------------
    max_nesting_depth: int = 0
    branch_points: int = 0
    #: True when the proof has no `by` block at all (a term-mode proof).
    term_mode: bool = False

    # -- comments ------------------------------------------------------
    comment_segments: int = 0
    block_comments: int = 0
    doc_comments: int = 0
    line_comments: int = 0
    comment_chars: int = 0

    # -- repetition ----------------------------------------------------
    # Degenerate generation shows up here first: a model that loops emits
    # the same line or the same tactic over and over, which looks like a
    # long, busy proof on every other metric.
    distinct_code_lines: int = 0
    #: 1 - distinct/total over non-empty lines. 0 means no repetition.
    line_repetition_rate: float = 0.0
    #: 1 - distinct/total over tactic invocations.
    tactic_repetition_rate: float = 0.0
    #: Longest run of identical consecutive lines; >2 is usually a loop.
    max_consecutive_duplicate_lines: int = 0
    #: Fraction of token trigrams that are not the first of their kind
    #: (the standard rep-3 measure for generated text).
    repeated_trigram_rate: float = 0.0

    @property
    def comment_ratio(self) -> float:
        """Comment characters as a fraction of the whole proof text."""
        return self.comment_chars / self.chars if self.chars else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chars": self.chars,
            "lines": self.lines,
            "code_lines": self.code_lines,
            "tokens": self.tokens,
            "max_line_chars": self.max_line_chars,
            "declarations": self.declarations,
            "auxiliary_declarations": self.auxiliary_declarations,
            "named_steps": self.named_steps,
            "local_dependency_depth": self.local_dependency_depth,
            "cited_lemmas": self.cited_lemmas,
            "calc_blocks": self.calc_blocks,
            "max_nesting_depth": self.max_nesting_depth,
            "branch_points": self.branch_points,
            "term_mode": self.term_mode,
            "comment_segments": self.comment_segments,
            "block_comments": self.block_comments,
            "doc_comments": self.doc_comments,
            "line_comments": self.line_comments,
            "comment_chars": self.comment_chars,
            "comment_ratio": round(self.comment_ratio, 4),
            "distinct_code_lines": self.distinct_code_lines,
            "line_repetition_rate": round(self.line_repetition_rate, 4),
            "tactic_repetition_rate": round(self.tactic_repetition_rate, 4),
            "max_consecutive_duplicate_lines": self.max_consecutive_duplicate_lines,
            "repeated_trigram_rate": round(self.repeated_trigram_rate, 4),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProofStructure":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def _max_bracket_depth(text: str) -> int:
    depth = best = 0
    for ch in text:
        if ch in _OPENERS:
            depth += 1
            best = max(best, depth)
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
    return best


def _max_indent_depth(text: str) -> int:
    """Deepest indentation level, in units of two spaces."""
    best = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        best = max(best, indent // 2)
    return best


def _local_dependency_depth(body: str) -> tuple[int, int]:
    """Longest chain through the proof's own named steps, and their count.

    ``have h1 : A := ...`` followed by ``have h2 : B := f h1`` followed by
    ``exact g h2`` is depth 3: each step's justification mentions the
    previous one. Built as a DAG over step names and walked for its
    longest path, so out-of-order and branching proofs are handled.

    Linear in the length of the proof. The obvious implementation --
    for each step, regex-search every earlier step name in its
    justification -- is quadratic in the number of steps, which measured
    at 177ms for a 400-step proof. Tokenizing each justification once and
    intersecting against the names seen so far does the same job in a
    single pass, and a degenerate model emitting hundreds of ``have``s is
    exactly the case that has to stay cheap.
    """
    steps: list[tuple[str, int]] = [(m.group("name"), m.start()) for m in _STEP_RE.finditer(body)]
    if not steps:
        return 0, 0

    depth_of: dict[str, int] = {}
    #: name -> index where it was introduced, populated as we go so a
    #: lookup can only ever find a step that came earlier.
    introduced_at: dict[str, int] = {}
    best = 0

    for index, (name, start) in enumerate(steps):
        end = steps[index + 1][1] if index + 1 < len(steps) else len(body)
        cited = set(_IDENTIFIER_RE.findall(body[start:end]))
        deepest = 0
        for other in cited:
            if other == name or other not in introduced_at:
                continue
            if introduced_at[other] < index:
                deepest = max(deepest, depth_of.get(other, 0))
        depth_of[name] = 1 + deepest
        best = max(best, depth_of[name])
        introduced_at.setdefault(name, index)

    # The final step of the proof consumes some named result, which adds
    # one more level to the chain the proof actually walks.
    tail = body[steps[-1][1] :]
    if _CLOSING_TACTIC_RE.search(tail):
        used = set(_IDENTIFIER_RE.findall(tail)) & set(depth_of)
        if used:
            best = max(best, 1 + max(depth_of[o] for o in used))
    return best, len(steps)


def analyze_proof(
    proof: str,
    language: str = "lean4",
    *,
    tactics: Sequence[str] | None = None,
) -> ProofStructure:
    """Measure one proof's structure.

    Comments are counted on the raw text and everything else on the
    comment-stripped text, so a heavily commented proof does not read as
    a structurally complex one.

    ``tactics`` lets a caller that has already run
    :func:`~ftp_eval.tactics.extract_tactics` hand the result in, so the
    proof is not scanned for tactics twice per attempt.
    """
    structure = ProofStructure()
    if not proof:
        return structure

    structure.chars = len(proof)
    structure.lines = len(proof.splitlines())
    structure.max_line_chars = max((len(ln) for ln in proof.splitlines()), default=0)

    # -- comments, from the raw text ----------------------------------
    doc_comments = _DOC_COMMENT_RE.findall(proof)
    all_blocks = _BLOCK_COMMENT_RE.findall(proof)
    line_comments = _LINE_COMMENT_RE.findall(_BLOCK_COMMENT_RE.sub(" ", proof))
    structure.doc_comments = len(doc_comments)
    # Every doc comment is also a block comment; report them separately
    # rather than double-counting.
    structure.block_comments = max(0, len(all_blocks) - len(doc_comments))
    structure.line_comments = len(line_comments)
    structure.comment_segments = len(all_blocks) + len(line_comments)
    structure.comment_chars = sum(len(c) for c in all_blocks) + sum(len(c) for c in line_comments)

    # -- everything else, from the stripped text ----------------------
    body = strip_comments(proof, language)
    structure.code_lines = sum(1 for ln in body.splitlines() if ln.strip())
    # Tokenized once here and reused for the trigram measure below.
    tokens = _TOKEN_RE.findall(body)
    structure.tokens = len(tokens)

    declarations = Counter(_DECL_RE.findall(body))
    structure.declarations = dict(declarations)
    # The theorem under test is one of these; anything beyond it is a
    # helper the model chose to write.
    structure.auxiliary_declarations = max(0, sum(declarations.values()) - 1)

    structure.local_dependency_depth, structure.named_steps = _local_dependency_depth(body)
    structure.cited_lemmas = len(set(_QUALIFIED_RE.findall(body)))
    structure.calc_blocks = len(re.findall(r"\bcalc\b", body))

    structure.max_nesting_depth = max(_max_bracket_depth(body), _max_indent_depth(body))
    structure.branch_points = len(_BRANCH_RE.findall(body))

    found_tactics = extract_tactics(body, language) if tactics is None else tactics
    # Absence of `by` is not enough on its own: under the
    # `continue_statement` convention the `by` lives in the *statement*,
    # so a perfectly ordinary tactic proof arrives as a bare fragment like
    # " simp". Recognized tactics settle it.
    structure.term_mode = not re.search(r"\bby\b", body) and not found_tactics

    _fill_repetition(structure, body, language, tokens=tokens, tactics=found_tactics)
    return structure


def _fill_repetition(
    structure: ProofStructure,
    body: str,
    language: str,
    *,
    tokens: Sequence[str] | None = None,
    tactics: Sequence[str] | None = None,
) -> None:
    """Measure how much of the proof is the same thing again.

    ``tokens`` and ``tactics`` are passed in by :func:`analyze_proof`,
    which has already computed both; recomputing them here would double
    the scanning cost for no new information.
    """
    stripped_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if stripped_lines:
        structure.distinct_code_lines = len(set(stripped_lines))
        structure.line_repetition_rate = 1.0 - structure.distinct_code_lines / len(stripped_lines)
        run = best_run = 1
        # strict=False on purpose: pairing a list with its own tail is
        # meant to be one element shorter.
        for previous, current in zip(stripped_lines, stripped_lines[1:], strict=False):
            run = run + 1 if current == previous else 1
            best_run = max(best_run, run)
        structure.max_consecutive_duplicate_lines = best_run if best_run > 1 else 0

    if tactics is None:
        tactics = extract_tactics(body, language)
    if tactics:
        structure.tactic_repetition_rate = 1.0 - len(set(tactics)) / len(tactics)

    if tokens is None:
        tokens = _TOKEN_RE.findall(body)
    if len(tokens) >= 3:
        # Counted with a rolling set rather than a materialized list of
        # n-2 tuples, so a huge proof does not allocate one tuple per
        # token position.
        seen: set[tuple[str, str, str]] = set()
        total = len(tokens) - 2
        for i in range(total):
            seen.add((tokens[i], tokens[i + 1], tokens[i + 2]))
        structure.repeated_trigram_rate = 1.0 - len(seen) / total


# ---------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------


#: Proof metrics aggregated into distributions, in report order. Also
#: what the correlation search scans -- which metrics exist is this
#: module's knowledge, not the statistics module's.
NUMERIC_FIELDS = (
    "lines",
    "code_lines",
    "tokens",
    "named_steps",
    "local_dependency_depth",
    "cited_lemmas",
    "auxiliary_declarations",
    "max_nesting_depth",
    "branch_points",
    "comment_segments",
    "comment_chars",
    "line_repetition_rate",
    "tactic_repetition_rate",
    "repeated_trigram_rate",
    "max_consecutive_duplicate_lines",
    # Statement-side, prefixed when merged into a result's structure dict.
    # Correlating solve rate against these is the difficulty proxy.
    "statement_binders",
    "statement_hypotheses",
    "statement_conclusion_tokens",
    "statement_quantifiers",
    "statement_connectives",
    "statement_cited_definitions",
    "statement_distinct_types",
    "statement_total_tokens",
    "statement_max_nesting_depth",
)


@dataclass(frozen=True)
class SampleDuplication:
    """How much of a k-sample run is the same proof submitted again.

    This bears directly on whether ``pass@k`` means anything. The
    estimator assumes k independent draws; if a model returns the same
    text five times, the real sample size is one and ``pass@5`` is
    reporting a precision the data does not have. A high
    ``mean_duplicate_fraction`` is a reason to distrust the k>1 numbers,
    not a minor curiosity.
    """

    tasks_with_multiple_samples: int = 0
    tasks_with_duplicates: int = 0
    #: Averaged over tasks: 1 - distinct proofs / samples.
    mean_duplicate_fraction: float = 0.0
    #: Tasks where every sample was byte-identical.
    tasks_all_identical: int = 0

    @property
    def undermines_pass_at_k(self) -> bool:
        """Whether the duplication is bad enough to caveat k>1 results."""
        return self.mean_duplicate_fraction >= 0.25 or self.tasks_all_identical > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks_with_multiple_samples": self.tasks_with_multiple_samples,
            "tasks_with_duplicates": self.tasks_with_duplicates,
            "tasks_all_identical": self.tasks_all_identical,
            "mean_duplicate_fraction": round(self.mean_duplicate_fraction, 4),
            "undermines_pass_at_k": self.undermines_pass_at_k,
        }


def sample_duplication(
    samples_by_task: Mapping[str, Sequence[str]],
) -> SampleDuplication:
    """Measure duplication among the k samples drawn for each task.

    ``samples_by_task`` maps a task id to that task's proof texts (or
    their fingerprints). Tasks with a single sample are skipped: there is
    nothing to be duplicated.
    """
    fractions: list[float] = []
    with_duplicates = 0
    all_identical = 0
    for texts in samples_by_task.values():
        if len(texts) < 2:
            continue
        distinct = len({t.strip() for t in texts})
        fraction = 1.0 - distinct / len(texts)
        fractions.append(fraction)
        if distinct < len(texts):
            with_duplicates += 1
        if distinct == 1:
            all_identical += 1
    return SampleDuplication(
        tasks_with_multiple_samples=len(fractions),
        tasks_with_duplicates=with_duplicates,
        tasks_all_identical=all_identical,
        mean_duplicate_fraction=sum(fractions) / len(fractions) if fractions else 0.0,
    )


@dataclass
class StructureStats:
    """Structural metrics across a run, as distributions.

    Split by outcome, because "verified proofs are 3 steps and failures
    are 30" is a finding, while the pooled average of the two is not.
    """

    proofs: int = 0
    term_mode_proofs: int = 0
    documented_proofs: int = 0
    metrics: dict[str, Distribution] = field(default_factory=dict)
    verified_metrics: dict[str, Distribution] = field(default_factory=dict)
    failed_metrics: dict[str, Distribution] = field(default_factory=dict)
    declaration_totals: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proofs": self.proofs,
            "term_mode_proofs": self.term_mode_proofs,
            "documented_proofs": self.documented_proofs,
            "declaration_totals": self.declaration_totals,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "verified_metrics": {k: v.to_dict() for k, v in self.verified_metrics.items()},
            "failed_metrics": {k: v.to_dict() for k, v in self.failed_metrics.items()},
        }

    def format_text(self) -> str:
        if not self.proofs:
            return "proof structure: not measured"
        lines = [
            "proof structure (%d proof(s); %d term-mode, %d carrying a doc comment)"
            % (self.proofs, self.term_mode_proofs, self.documented_proofs),
            "  %-32s %7s %7s %7s %7s   %8s %8s"
            % ("metric", "mean", "median", "p90", "max", "ok-mean", "bad-mean"),
        ]
        for name in NUMERIC_FIELDS:
            dist = self.metrics.get(name)
            if dist is None or not dist.count:
                continue
            ok = self.verified_metrics.get(name)
            bad = self.failed_metrics.get(name)
            lines.append(
                "  %-32s %7.2f %7.1f %7.1f %7.0f   %8s %8s"
                % (
                    name,
                    dist.mean,
                    dist.median,
                    dist.p90,
                    dist.maximum,
                    "n/a" if not (ok and ok.count) else "%8.2f" % ok.mean,
                    "n/a" if not (bad and bad.count) else "%8.2f" % bad.mean,
                )
            )
        if self.declaration_totals:
            ranked = sorted(self.declaration_totals.items(), key=lambda kv: -kv[1])
            lines.append("  declarations: " + ", ".join("%s=%d" % kv for kv in ranked))
        return "\n".join(lines)


def aggregate_structure(
    entries: Iterable[tuple[Mapping[str, Any], bool, bool]],
) -> StructureStats:
    """Aggregate ``(structure_dict, verified, scoreable)`` triples."""
    stats = StructureStats()
    pooled: dict[str, list[float]] = {name: [] for name in NUMERIC_FIELDS}
    verified: dict[str, list[float]] = {name: [] for name in NUMERIC_FIELDS}
    failed: dict[str, list[float]] = {name: [] for name in NUMERIC_FIELDS}
    declarations: Counter[str] = Counter()

    for raw, is_verified, scoreable in entries:
        if not raw:
            continue
        stats.proofs += 1
        if raw.get("term_mode"):
            stats.term_mode_proofs += 1
        if int(raw.get("doc_comments") or 0) > 0:
            stats.documented_proofs += 1
        declarations.update({k: int(v) for k, v in (raw.get("declarations") or {}).items()})
        for name in NUMERIC_FIELDS:
            value = raw.get(name)
            if value is None:
                continue
            pooled[name].append(float(value))
            if scoreable:
                (verified if is_verified else failed)[name].append(float(value))

    stats.metrics = {k: distribution(v) for k, v in pooled.items() if v}
    stats.verified_metrics = {k: distribution(v) for k, v in verified.items() if v}
    stats.failed_metrics = {k: distribution(v) for k, v in failed.items() if v}
    stats.declaration_totals = dict(declarations)
    return stats
