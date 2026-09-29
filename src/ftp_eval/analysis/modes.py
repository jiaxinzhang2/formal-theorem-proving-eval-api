"""What *kind* of outcome an attempt was -- for passes and failures alike.

Both directions are classified, because both carry information a pass rate
throws away.

**Failures.** :class:`~ftp_eval.types.ErrorKind` is deliberately coarse so
it can mean the same thing across provers, and that coarseness hides the
distinctions that tell you what to do next. See :class:`FailureMode`.

**Successes.** A proof that verified still has a shape, and the shape is
most of what you learn from it. "Solved 40% of the benchmark" means
something completely different when 90% of those solves are a single
``omega`` call than when they are structured multi-step arguments with
intermediate lemmas. See :class:`SuccessMode`. This is also how you notice
a benchmark quietly turning into a computation exercise: the share of
``brute_force_decide`` climbs.

---

Fine-grained failure-mode classification for failed proofs.

:class:`~ftp_eval.types.ErrorKind` is deliberately coarse so it can mean
the same thing across provers. That coarseness hides the distinctions that
actually tell you what to do next:

* **A hallucinated lemma name and a real type error** are both
  ``unknown_identifier``/``type`` — but one means the model invented
  ``Nat.add_le_of_lt'`` and the other means it misunderstood the goal.
* **A truncated completion and a genuine syntax error** are both
  ``syntax`` — but the first means raise ``max_tokens``, and only the
  second is a capability result. Conflating them makes a budget problem
  look like a weak model.
* **A missing typeclass instance and a wrong argument** are both ``type``,
  and they call for completely different fixes.
* **`linarith` failing and `simp` making no progress** are both
  ``tactic_failed``, and which automation is running out of road is the
  single most useful thing to know about a failing benchmark.

So each failure also gets a :class:`FailureMode`, and each mode carries an
:class:`Attribution` saying whose problem it is. "40% of failures were
truncation" is an instruction to change the harness; "40% were
hallucinated lemma names" is an instruction to change the prompt or give
the model retrieval.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..types import Diagnostic, ErrorKind, Severity, Status

__all__ = [
    "Attribution",
    "FailureMode",
    "classify_failure",
    "hallucinated_names",
    "FailureModeStats",
    "aggregate_failure_modes",
    "looks_truncated",
    "SuccessMode",
    "classify_success",
    "SuccessModeStats",
    "aggregate_success_modes",
]


class Attribution(str, Enum):
    """Whose problem a failure is. The cut that decides what to fix."""

    #: The model did not produce a correct proof. A real capability result.
    MODEL = "model"
    #: The generation budget ran out: truncated output, prover timeout,
    #: heartbeat limit. Fix the harness before reading these as capability.
    BUDGET = "budget"
    #: Our setup: missing toolchain, bad project, assembly mismatch.
    HARNESS = "harness"
    #: The proof was reward hacking.
    SOUNDNESS = "soundness"
    UNKNOWN = "unknown"


class FailureMode(str, Enum):
    """What specifically went wrong, for Lean 4."""

    # -- syntax / truncation ------------------------------------------
    TRUNCATED_OUTPUT = "truncated_output"
    UNEXPECTED_TOKEN = "unexpected_token"
    INCOMPLETE_SYNTAX = "incomplete_syntax"
    BAD_INDENTATION = "bad_indentation"
    EMPTY_PROOF = "empty_proof"

    # -- name resolution ----------------------------------------------
    UNKNOWN_LEMMA = "unknown_lemma"
    UNKNOWN_TACTIC = "unknown_tactic"
    UNKNOWN_NAMESPACE = "unknown_namespace"
    AMBIGUOUS_NAME = "ambiguous_name"

    # -- elaboration --------------------------------------------------
    TYPE_MISMATCH = "type_mismatch"
    APPLICATION_MISMATCH = "application_mismatch"
    MISSING_INSTANCE = "missing_instance"
    FUNCTION_EXPECTED = "function_expected"
    UNRESOLVED_METAVARIABLE = "unresolved_metavariable"
    MOTIVE_NOT_TYPE_CORRECT = "motive_not_type_correct"
    UNIVERSE_ISSUE = "universe_issue"
    NUMERAL_TYPE = "numeral_type"

    # -- automation gave up -------------------------------------------
    LINARITH_FAILED = "linarith_failed"
    NLINARITH_FAILED = "nlinarith_failed"
    OMEGA_FAILED = "omega_failed"
    SIMP_NO_PROGRESS = "simp_no_progress"
    RW_PATTERN_NOT_FOUND = "rw_pattern_not_found"
    DECIDE_FAILED = "decide_failed"
    POSITIVITY_FAILED = "positivity_failed"
    RING_FAILED = "ring_failed"
    AESOP_FAILED = "aesop_failed"
    NORM_NUM_FAILED = "norm_num_failed"
    APPLY_FAILED = "apply_failed"
    EXACT_TYPE_MISMATCH = "exact_type_mismatch"
    OTHER_TACTIC_FAILED = "other_tactic_failed"

    # -- incomplete ---------------------------------------------------
    UNSOLVED_GOALS = "unsolved_goals"
    PLACEHOLDER_LEFT = "placeholder_left"

    # -- resource -----------------------------------------------------
    HEARTBEAT_EXCEEDED = "heartbeat_exceeded"
    RECURSION_DEPTH = "recursion_depth"
    WALL_CLOCK_TIMEOUT = "wall_clock_timeout"
    OUT_OF_MEMORY = "out_of_memory"

    # -- not the model's fault ----------------------------------------
    REWARD_HACKING = "reward_hacking"
    TOOLCHAIN_ERROR = "toolchain_error"
    HARNESS_ERROR = "harness_error"
    LANGUAGE_MISMATCH = "language_mismatch"
    UNCLASSIFIED = "unclassified"

    @property
    def attribution(self) -> Attribution:
        return _ATTRIBUTION[self]

    @property
    def description(self) -> str:
        return _DESCRIPTIONS.get(self, self.value.replace("_", " "))


_ATTRIBUTION: dict[FailureMode, Attribution] = {
    # Truncation and resource exhaustion are budget problems, not results.
    FailureMode.TRUNCATED_OUTPUT: Attribution.BUDGET,
    FailureMode.HEARTBEAT_EXCEEDED: Attribution.BUDGET,
    FailureMode.WALL_CLOCK_TIMEOUT: Attribution.BUDGET,
    FailureMode.RECURSION_DEPTH: Attribution.BUDGET,
    FailureMode.OUT_OF_MEMORY: Attribution.BUDGET,
    FailureMode.REWARD_HACKING: Attribution.SOUNDNESS,
    FailureMode.TOOLCHAIN_ERROR: Attribution.HARNESS,
    FailureMode.HARNESS_ERROR: Attribution.HARNESS,
    FailureMode.LANGUAGE_MISMATCH: Attribution.HARNESS,
    FailureMode.UNCLASSIFIED: Attribution.UNKNOWN,
}
for _mode in FailureMode:
    _ATTRIBUTION.setdefault(_mode, Attribution.MODEL)


_DESCRIPTIONS: dict[FailureMode, str] = {
    FailureMode.TRUNCATED_OUTPUT: (
        "the completion was cut off mid-proof; raise max_tokens before reading "
        "this as a capability result"
    ),
    FailureMode.UNKNOWN_LEMMA: (
        "cited a lemma that does not exist -- a hallucinated name, the most "
        "common Lean failure for language models"
    ),
    FailureMode.UNKNOWN_TACTIC: "invoked a tactic that does not exist in this Mathlib",
    FailureMode.MISSING_INSTANCE: "a typeclass instance could not be synthesized",
    FailureMode.UNSOLVED_GOALS: "the proof ran to the end with goals still open",
    FailureMode.SIMP_NO_PROGRESS: "`simp` could not rewrite anything at that point",
    FailureMode.RW_PATTERN_NOT_FOUND: "`rw` could not find its pattern in the goal",
    FailureMode.MOTIVE_NOT_TYPE_CORRECT: (
        "a rewrite was not type correct -- usually rewriting under a dependent type"
    ),
    FailureMode.HEARTBEAT_EXCEEDED: "elaboration hit the heartbeat limit",
    FailureMode.WALL_CLOCK_TIMEOUT: "the prover was still working when the budget ran out",
    FailureMode.REWARD_HACKING: "accepted by the prover but not a proof of the goal",
}


#: (regex, mode), first match wins. Order matters: the specific automation
#: failures come before the generic tactic failure, and name-resolution
#: errors before the type errors they technically also are.
_PATTERNS: tuple[tuple[re.Pattern[str], FailureMode], ...] = tuple(
    (re.compile(pattern, re.I), mode)
    for pattern, mode in (
        # syntax / truncation
        (r"unexpected end of input", FailureMode.TRUNCATED_OUTPUT),
        (r"unexpected token .*; expected", FailureMode.UNEXPECTED_TOKEN),
        (r"unexpected token", FailureMode.UNEXPECTED_TOKEN),
        (r"expected (?:term|command|identifier|'\S+')", FailureMode.INCOMPLETE_SYNTAX),
        (r"unexpected indentation|expected .* indentation|ill-formed", FailureMode.BAD_INDENTATION),
        # name resolution
        (r"unknown tactic", FailureMode.UNKNOWN_TACTIC),
        (r"unknown (?:identifier|constant)", FailureMode.UNKNOWN_LEMMA),
        (r"unknown namespace", FailureMode.UNKNOWN_NAMESPACE),
        (r"ambiguous, possible interpretations|overloaded", FailureMode.AMBIGUOUS_NAME),
        # automation
        # nlinarith first, and \b on linarith: without both, "nlinarith
        # failed" matches the linarith pattern on its substring.
        (r"\bnlinarith failed|\bpolyrith failed", FailureMode.NLINARITH_FAILED),
        (r"\blinarith failed", FailureMode.LINARITH_FAILED),
        (r"omega (?:could not|failed)", FailureMode.OMEGA_FAILED),
        (r"simp made no progress|simp_all made no progress", FailureMode.SIMP_NO_PROGRESS),
        (r"did not find instance of the pattern|motive is not type correct", FailureMode.RW_PATTERN_NOT_FOUND),
        (r"failed to (?:reduce to|prove) .*decid|decide failed|decidable instance", FailureMode.DECIDE_FAILED),
        (r"positivity failed|not positive", FailureMode.POSITIVITY_FAILED),
        (r"ring_nf failed|ring failed|field_simp", FailureMode.RING_FAILED),
        (r"aesop (?:failed|could not)", FailureMode.AESOP_FAILED),
        (r"norm_num (?:failed|extension)", FailureMode.NORM_NUM_FAILED),
        (r"apply .*failed|could not unify|failed to unify", FailureMode.APPLY_FAILED),
        # incomplete
        (r"unsolved goals", FailureMode.UNSOLVED_GOALS),
        (r"uses 'sorry'|declaration uses sorry", FailureMode.PLACEHOLDER_LEFT),
        # resource
        (r"\(?deterministic\)?\s+timeout|maxHeartbeats", FailureMode.HEARTBEAT_EXCEEDED),
        (r"maximum recursion depth|deep recursion|stack overflow", FailureMode.RECURSION_DEPTH),
        (r"out of memory|allocation failed", FailureMode.OUT_OF_MEMORY),
        # elaboration -- after the above, since these messages are generic
        (r"failed to synthesize", FailureMode.MISSING_INSTANCE),
        (r"application type mismatch", FailureMode.APPLICATION_MISMATCH),
        (r"type mismatch.*\bexact\b|exact .*type mismatch", FailureMode.EXACT_TYPE_MISMATCH),
        (r"type mismatch", FailureMode.TYPE_MISMATCH),
        (r"function expected", FailureMode.FUNCTION_EXPECTED),
        (r"don't know how to synthesize|metavariable|instance problem stuck", FailureMode.UNRESOLVED_METAVARIABLE),
        (r"motive is not type correct", FailureMode.MOTIVE_NOT_TYPE_CORRECT),
        (r"universe", FailureMode.UNIVERSE_ISSUE),
        (r"OfNat|numeral|cannot coerce", FailureMode.NUMERAL_TYPE),
        (r"tactic .*failed|failed to", FailureMode.OTHER_TACTIC_FAILED),
    )
)

#: `unknown identifier 'Nat.foo'` / `unknown constant 'Foo.bar'`
_UNKNOWN_NAME_RE = re.compile(
    r"unknown (?:identifier|constant|tactic)\s*'([^']+)'", re.I
)

_OPENERS = "([{⟨⦃"
_CLOSERS = ")]}⟩⦄"


def looks_truncated(proof: str) -> bool:
    """Whether a completion appears to have been cut off mid-proof.

    Three independent signals, any of which is enough: unbalanced
    brackets, a trailing token that cannot end a proof, or an ending in
    the middle of a word after a tactic keyword. Cheap and imperfect, but
    it separates a budget problem from a capability result, and getting
    that wrong is expensive in both directions.
    """
    text = proof.rstrip()
    if not text:
        return False

    depth = 0
    for ch in text:
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
    if depth > 0:
        return True

    # A proof that ends on a connector or an opening keyword was going
    # somewhere and did not get there.
    if re.search(
        r"(?:,|:=|:|\+|-|\*|/|←|<-|→|->|\bby\b|\bwith\b|\bat\b|\bfrom\b|\bhave\b|"
        r"\bshow\b|\brw\b|\bexact\b|\bapply\b|\brefine\b|\[)\s*$",
        text,
    ):
        return True
    return False


def hallucinated_names(diagnostics: Iterable[Diagnostic]) -> list[str]:
    """Names the prover said do not exist.

    Aggregated across a run, this is a ranked list of what the model
    invents -- directly useful for prompt work, retrieval, or spotting a
    Mathlib version mismatch where the names were real but renamed.
    """
    found: list[str] = []
    for d in diagnostics:
        for match in _UNKNOWN_NAME_RE.finditer(d.message):
            found.append(match.group(1))
    return found


def classify_failure(
    status: Status,
    error_kind: ErrorKind | None,
    diagnostics: Sequence[Diagnostic] = (),
    *,
    proof: str = "",
    soundness_ok: bool = True,
) -> FailureMode | None:
    """Classify one failed attempt. ``None`` for a clean success.

    Checked in order of what would be misleading to miss: reward hacking
    first, then harness problems, then truncation (because a truncated
    proof produces a *syntax* error that would otherwise be read as the
    model writing bad Lean), then the message patterns.
    """
    if status is Status.VERIFIED:
        return None
    if status is Status.REJECTED or not soundness_ok:
        return FailureMode.REWARD_HACKING
    if status is Status.TIMEOUT:
        return FailureMode.WALL_CLOCK_TIMEOUT
    if status is Status.SKIPPED:
        return FailureMode.LANGUAGE_MISMATCH
    if error_kind is ErrorKind.TOOLCHAIN:
        return FailureMode.TOOLCHAIN_ERROR
    if error_kind is ErrorKind.HARNESS or status is Status.ERROR:
        return FailureMode.HARNESS_ERROR
    if not proof.strip():
        return FailureMode.EMPTY_PROOF

    errors = [d for d in diagnostics if d.severity is Severity.ERROR]
    messages = "\n".join(d.message for d in errors) if errors else ""

    # Truncation has to be settled before the message patterns: a cut-off
    # completion produces a syntax error, and calling that "bad Lean"
    # turns a max_tokens setting into a capability claim.
    if re.search(r"unexpected end of input", messages, re.I) or (
        looks_truncated(proof)
        and (not messages or re.search(r"unexpected token|expected", messages, re.I))
    ):
        return FailureMode.TRUNCATED_OUTPUT

    for pattern, mode in _PATTERNS:
        if pattern.search(messages):
            return mode

    # Fall back to the coarse kind rather than guessing a specific mode.
    fallback = {
        ErrorKind.UNSOLVED_GOALS: FailureMode.UNSOLVED_GOALS,
        ErrorKind.UNKNOWN_IDENTIFIER: FailureMode.UNKNOWN_LEMMA,
        ErrorKind.SYNTAX: FailureMode.UNEXPECTED_TOKEN,
        ErrorKind.TYPE: FailureMode.TYPE_MISMATCH,
        ErrorKind.TACTIC_FAILED: FailureMode.OTHER_TACTIC_FAILED,
        ErrorKind.INCOMPLETE: FailureMode.PLACEHOLDER_LEFT,
        ErrorKind.RESOURCE_LIMIT: FailureMode.HEARTBEAT_EXCEEDED,
        ErrorKind.TIMEOUT: FailureMode.WALL_CLOCK_TIMEOUT,
        ErrorKind.SOUNDNESS: FailureMode.REWARD_HACKING,
    }
    return fallback.get(error_kind or ErrorKind.UNKNOWN, FailureMode.UNCLASSIFIED)


@dataclass
class FailureModeStats:
    """Failure modes over a run, with the attribution cut."""

    failures: int = 0
    modes: dict[str, int] = field(default_factory=dict)
    attributions: dict[str, int] = field(default_factory=dict)
    #: Hallucinated names, ranked. What the model invents most often.
    invented_names: dict[str, int] = field(default_factory=dict)
    #: Mode counts per model, when a run mixes models.
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Position of the first error, normalized by proof length. Near 0
    #: means models break immediately (syntax); near 1 means they get most
    #: of the way and fail at the last step.
    mean_first_error_position: float | None = None

    @property
    def model_attributable(self) -> int:
        return self.attributions.get(Attribution.MODEL.value, 0)

    @property
    def budget_attributable(self) -> int:
        return self.attributions.get(Attribution.BUDGET.value, 0)

    @property
    def budget_fraction(self) -> float:
        """Share of failures that are budget, not capability.

        Above roughly 10% the headline number is measuring your harness
        as much as the model.
        """
        return self.budget_attributable / self.failures if self.failures else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "failures": self.failures,
            "modes": self.modes,
            "attributions": self.attributions,
            "budget_fraction": round(self.budget_fraction, 4),
            "invented_names": self.invented_names,
            "by_model": self.by_model,
            "mean_first_error_position": (
                None
                if self.mean_first_error_position is None
                else round(self.mean_first_error_position, 4)
            ),
        }

    def format_text(self, limit: int = 15) -> str:
        if not self.failures:
            return "failure modes: no failures to classify"
        lines = ["failure modes (%d failed attempt(s))" % self.failures]
        for mode, count in sorted(self.modes.items(), key=lambda kv: -kv[1])[:limit]:
            try:
                enum_mode = FailureMode(mode)
                attribution = enum_mode.attribution.value
                description = _DESCRIPTIONS.get(enum_mode, "")
            except ValueError:
                attribution, description = "?", ""
            lines.append(
                "  %-24s %5d  %5.1f%%  [%s]%s"
                % (
                    mode,
                    count,
                    100 * count / self.failures,
                    attribution,
                    "  " + description if description else "",
                )
            )
        remaining = len(self.modes) - min(limit, len(self.modes))
        if remaining > 0:
            lines.append("  ... and %d more mode(s)" % remaining)
        lines.append(
            "  attribution: "
            + ", ".join(
                "%s=%d" % kv
                for kv in sorted(self.attributions.items(), key=lambda kv: -kv[1])
            )
        )
        if self.budget_fraction >= 0.10:
            lines.append(
                "  WARNING: %.0f%% of failures are budget, not capability (truncation, "
                "timeouts, heartbeat limits). Fix those before quoting this as a model "
                "result." % (100 * self.budget_fraction)
            )
        if self.invented_names:
            ranked = sorted(self.invented_names.items(), key=lambda kv: -kv[1])[:8]
            lines.append(
                "  most-invented names: " + ", ".join("%s(%d)" % kv for kv in ranked)
            )
        if self.mean_first_error_position is not None:
            lines.append(
                "  mean first-error position: %.2f of the way through the proof"
                % self.mean_first_error_position
            )
        return "\n".join(lines)


def aggregate_failure_modes(
    entries: Iterable[tuple[FailureMode | None, Sequence[Diagnostic], str | None, int, int]],
) -> FailureModeStats:
    """Aggregate ``(mode, diagnostics, model, first_error_line, proof_lines)``."""
    stats = FailureModeStats()
    modes: Counter[str] = Counter()
    attributions: Counter[str] = Counter()
    invented: Counter[str] = Counter()
    by_model: dict[str, Counter[str]] = {}
    positions: list[float] = []

    for mode, diagnostics, model, first_error_line, proof_lines in entries:
        if mode is None:
            continue
        stats.failures += 1
        modes[mode.value] += 1
        attributions[mode.attribution.value] += 1
        invented.update(hallucinated_names(diagnostics))
        if model:
            by_model.setdefault(model, Counter())[mode.value] += 1
        if first_error_line and proof_lines > 0:
            positions.append(min(1.0, max(0.0, first_error_line / proof_lines)))

    stats.modes = dict(modes)
    stats.attributions = dict(attributions)
    stats.invented_names = dict(invented)
    stats.by_model = {m: dict(c) for m, c in by_model.items()}
    stats.mean_first_error_position = (
        sum(positions) / len(positions) if positions else None
    )
    return stats


# ---------------------------------------------------------------------
# Success modes
# ---------------------------------------------------------------------


class SuccessMode(str, Enum):
    """What *kind* of proof succeeded.

    Ordered roughly by how much mathematical work the model did, which is
    the axis a pass rate collapses. Assigned by the most structurally
    demanding feature present, so a proof with both ``induction`` and a
    ``calc`` chain reports the induction.
    """

    #: A single automation call closed it: `by omega`, `by simp`.
    ONE_LINER_AUTOMATION = "one_liner_automation"
    #: Closed by evaluation rather than argument: `decide`, `norm_num` on a
    #: closed numeric claim. A rising share means the benchmark is drifting
    #: toward computation.
    BRUTE_FORCE_DECIDE = "brute_force_decide"
    #: A direct term, no tactic block at all.
    TERM_MODE = "term_mode"
    #: A handful of tactics in sequence, no named intermediate results.
    SHORT_TACTIC_CHAIN = "short_tactic_chain"
    #: Many tactics, still no named steps -- searching rather than arguing.
    LONG_TACTIC_CHAIN = "long_tactic_chain"
    #: Named intermediate results (`have`, `suffices`) chained together.
    STRUCTURED_WITH_STEPS = "structured_with_steps"
    #: A `calc` chain of (in)equalities.
    CALC_CHAIN = "calc_chain"
    #: Splits into cases (`cases`, `rcases`, `by_cases`).
    CASE_ANALYSIS = "case_analysis"
    #: Proof by induction.
    INDUCTION = "induction"
    #: Declares its own helper lemmas.
    AUXILIARY_LEMMAS = "auxiliary_lemmas"
    UNCLASSIFIED = "unclassified"

    @property
    def description(self) -> str:
        return _SUCCESS_DESCRIPTIONS.get(self, self.value.replace("_", " "))

    @property
    def is_substantive(self) -> bool:
        """Whether the proof did structural work beyond calling automation.

        Not a quality judgement -- a one-line ``omega`` is the right proof
        for an arithmetic goal. It is a description, so that two models
        with the same pass rate can be told apart.
        """
        return self in (
            SuccessMode.STRUCTURED_WITH_STEPS,
            SuccessMode.CALC_CHAIN,
            SuccessMode.CASE_ANALYSIS,
            SuccessMode.INDUCTION,
            SuccessMode.AUXILIARY_LEMMAS,
        )


_SUCCESS_DESCRIPTIONS: dict[SuccessMode, str] = {
    SuccessMode.ONE_LINER_AUTOMATION: "one automation call closed the goal",
    SuccessMode.BRUTE_FORCE_DECIDE: "closed by evaluation rather than argument",
    SuccessMode.TERM_MODE: "a direct term, no tactic block",
    SuccessMode.SHORT_TACTIC_CHAIN: "a few tactics, no named intermediate results",
    SuccessMode.LONG_TACTIC_CHAIN: "many tactics, no named steps -- searching, not arguing",
    SuccessMode.STRUCTURED_WITH_STEPS: "named intermediate results chained together",
    SuccessMode.CALC_CHAIN: "a calc chain of (in)equalities",
    SuccessMode.CASE_ANALYSIS: "splits the goal into cases",
    SuccessMode.INDUCTION: "proof by induction",
    SuccessMode.AUXILIARY_LEMMAS: "declares its own helper lemmas",
}

#: Tactics that close a goal by computing rather than reasoning.
_DECISION_TACTICS = frozenset({"decide", "native_decide"})

#: Tactics that on their own can close a goal.
_AUTOMATION_TACTICS = frozenset(
    {
        "simp", "simp_all", "simpa", "omega", "linarith", "nlinarith", "polyrith",
        "norm_num", "positivity", "aesop", "tauto", "trivial", "rfl", "ring",
        "ring_nf", "decide", "native_decide", "assumption", "measurability",
        "continuity", "field_simp", "gcongr", "bound",
    }
)

_CASE_TACTICS = frozenset({"cases", "rcases", "by_cases", "obtain", "interval_cases", "fin_cases", "split_ifs", "match"})
_INDUCTION_TACTICS = frozenset({"induction", "induction'"})


def classify_success(
    tactics: Sequence[str],
    structure: Mapping[str, Any],
) -> SuccessMode:
    """Classify a verified proof by its shape.

    Reads the already-computed ``structure`` dict rather than re-parsing
    the proof, so this costs nothing on top of the metrics that were
    collected anyway.
    """
    unique = set(tactics)
    named_steps = int(structure.get("named_steps") or 0)
    auxiliary = int(structure.get("auxiliary_declarations") or 0)
    calc_blocks = int(structure.get("calc_blocks") or 0)
    term_mode = bool(structure.get("term_mode"))

    # Most structurally demanding feature wins.
    if auxiliary > 0:
        return SuccessMode.AUXILIARY_LEMMAS
    if unique & _INDUCTION_TACTICS:
        return SuccessMode.INDUCTION
    if calc_blocks > 0:
        return SuccessMode.CALC_CHAIN
    if unique & _CASE_TACTICS:
        return SuccessMode.CASE_ANALYSIS
    if named_steps > 0:
        return SuccessMode.STRUCTURED_WITH_STEPS

    if term_mode:
        return SuccessMode.TERM_MODE
    if not tactics:
        return SuccessMode.UNCLASSIFIED

    # A single decision-procedure call is worth separating from other
    # one-liners: it means the goal was decidable by computation.
    if len(tactics) == 1:
        return (
            SuccessMode.BRUTE_FORCE_DECIDE
            if tactics[0] in _DECISION_TACTICS
            else SuccessMode.ONE_LINER_AUTOMATION
        )
    if unique <= _DECISION_TACTICS:
        return SuccessMode.BRUTE_FORCE_DECIDE
    if len(tactics) <= 2 and unique <= _AUTOMATION_TACTICS:
        return SuccessMode.ONE_LINER_AUTOMATION
    return (
        SuccessMode.SHORT_TACTIC_CHAIN if len(tactics) <= 5 else SuccessMode.LONG_TACTIC_CHAIN
    )


@dataclass
class SuccessModeStats:
    """Success modes over a run."""

    successes: int = 0
    modes: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def substantive(self) -> int:
        return sum(
            count
            for mode, count in self.modes.items()
            if _safe_success_mode(mode) and _safe_success_mode(mode).is_substantive  # type: ignore[union-attr]
        )

    @property
    def substantive_fraction(self) -> float:
        """Share of passes that did structural work beyond automation.

        Two models with the same pass rate and very different values here
        are not equally good at theorem proving, and the pass rate alone
        cannot say so.
        """
        return self.substantive / self.successes if self.successes else 0.0

    @property
    def automation_fraction(self) -> float:
        one_liners = self.modes.get(SuccessMode.ONE_LINER_AUTOMATION.value, 0)
        brute = self.modes.get(SuccessMode.BRUTE_FORCE_DECIDE.value, 0)
        return (one_liners + brute) / self.successes if self.successes else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "successes": self.successes,
            "modes": self.modes,
            "by_model": self.by_model,
            "substantive_fraction": round(self.substantive_fraction, 4),
            "automation_fraction": round(self.automation_fraction, 4),
        }

    def format_text(self) -> str:
        if not self.successes:
            return "success modes: no verified proofs to classify"
        lines = ["success modes (%d verified proof(s))" % self.successes]
        for mode, count in sorted(self.modes.items(), key=lambda kv: -kv[1]):
            resolved = _safe_success_mode(mode)
            lines.append(
                "  %-24s %5d  %5.1f%%  %s"
                % (
                    mode,
                    count,
                    100 * count / self.successes,
                    resolved.description if resolved else "",
                )
            )
        lines.append(
            "  %.0f%% closed by automation alone; %.0f%% did structural work"
            % (100 * self.automation_fraction, 100 * self.substantive_fraction)
        )
        if self.automation_fraction >= 0.8:
            lines.append(
                "  NOTE: almost every pass is a single automation call. The benchmark may "
                "be measuring tactic coverage more than proving ability."
            )
        return "\n".join(lines)


def _safe_success_mode(value: str) -> SuccessMode | None:
    try:
        return SuccessMode(value)
    except ValueError:
        return None


def aggregate_success_modes(
    entries: Iterable[tuple[SuccessMode | None, str | None]],
) -> SuccessModeStats:
    """Aggregate ``(success_mode, model)`` pairs."""
    stats = SuccessModeStats()
    modes: Counter[str] = Counter()
    by_model: dict[str, Counter[str]] = {}
    for mode, model in entries:
        if mode is None:
            continue
        stats.successes += 1
        modes[mode.value] += 1
        if model:
            by_model.setdefault(model, Counter())[mode.value] += 1
    stats.modes = dict(modes)
    stats.by_model = {m: dict(c) for m, c in by_model.items()}
    return stats


def stats_from_results(results: Iterable[Mapping[str, Any]]) -> FailureModeStats:
    """Build failure-mode stats from serialized results."""
    entries = []
    for r in results:
        raw_mode = r.get("failure_mode")
        if not raw_mode:
            continue
        try:
            mode = FailureMode(raw_mode)
        except ValueError:
            mode = FailureMode.UNCLASSIFIED
        diagnostics = tuple(
            Diagnostic(
                severity=Severity(d.get("severity", "error")),
                message=d.get("message", ""),
                line=d.get("line"),
                column=d.get("column"),
            )
            for d in r.get("diagnostics") or ()
        )
        first_line = next((d.line for d in diagnostics if d.line), None)
        entries.append(
            (
                mode,
                diagnostics,
                r.get("model"),
                first_line or 0,
                int((r.get("structure") or {}).get("lines") or 0),
            )
        )
    return aggregate_failure_modes(entries)
