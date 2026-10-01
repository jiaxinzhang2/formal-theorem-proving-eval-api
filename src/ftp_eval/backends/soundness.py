"""Screening Lean source for constructs that make a prover's "yes" hollow.

Mostly, but **not only**, about proofs. A problem file and an answer file
are both Lean source, and both can contain something that makes the
prover's verdict mean less than it appears to -- which is why this module
sits below both APIs rather than inside the proving one:

* a **frozen-target answer** uses ``ANSWER_HACK_CLASSES``: placeholders,
  new axioms, kernel bypasses, resource-limit changes and elaboration tricks.
  Statement-tampering and definition-shadowing rules do not apply to that
  interface; checking.screening enforces its namespace and syntax policy;
* a **lower-level proof attempt** uses ``PROOF_HACK_CLASSES``: a
  placeholder the prover only warns about, the goal assumed as a fresh
  axiom, the kernel turned off, a term the statement depends on redefined;
* a **problem** uses ``STATEMENT_HACK_CLASSES`` -- a setter's helper `def`
  marked ``unsafe``, a helper lemma
  closed by ``native_decide``, a ``partial def`` whose termination is
  unchecked, ``#exit`` truncating the file, ``autoImplicit`` weakening the
  goal. A problem resting on unchecked computation is the setter's bug, and
  no amount of checking the answers will find it.

The proof-only exclusions are documented beside ``STATEMENT_HACK_CLASSES``:
a problem file's ``sorry`` is the hole a participant fills, its
``variable`` bindings are ordinary Lean, and the heartbeat cap is about
proof search rather than about what anything means. Screening a statement
with the proof-side set is not a harmless superset -- it reports every
well-formed problem as malformed.

A prover's "yes" is only a reward signal if it means what it looks like.
It very often does not, and the ways it fails are not exotic -- they are
what a model under optimization pressure finds first:

* leave a placeholder that the prover only warns about;
* assume the goal as a fresh axiom, then cite it;
* turn off the kernel, or route the proof through the compiler instead;
* redefine a term the statement depends on, so the statement now says
  something else;
* prove a different theorem under the required name.

Every check below corresponds to one of those. They are grouped by
:class:`HackClass` so a report can say *how* a proof cheated, not just
that it did.

Two limits, stated because the value of this module is in being trusted:

1. **These checks are syntactic.** They catch the known tricks. A novel
   one will get past them, which is why the Lean backend also runs a
   kernel-level axiom audit (``#print axioms``) -- that one sees through
   any amount of indirection, including a ``sorry`` reached through a
   helper lemma three files away.
2. **They are tuned to flag rather than miss.** A false flag is a
   ``rejected`` row a human can review; a miss is a wrong number in a
   paper. Where the two conflict, this module flags.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

from .comments import strip_comments

__all__ = [
    "SoundnessReport",
    "HackClass",
    "STATEMENT_HACK_CLASSES",
    "PROOF_HACK_CLASSES",
    "ANSWER_HACK_CLASSES",
    "HackPattern",
    "strip_comments",  # re-exported: callers of the screen usually want both
    "screen_source",
    "declared_names",
    "statement_identifiers",
    "PATTERNS",
    "LEGITIMATE_AXIOMS",
    "audit_axioms",
]


@dataclass(frozen=True)
class SoundnessReport:
    """Why a syntactically accepted proof may still not count.

    Formal verification is only a trustworthy reward signal if the thing
    that was proved is the thing that was asked. Every check that can
    fire here corresponds to a known way of gaming a prover: leaving
    ``sorry`` in, asserting the goal as a fresh axiom, weakening the
    hypotheses, or proving a different theorem entirely.
    """

    ok: bool = True
    violations: tuple[str, ...] = ()

    def with_violation(self, violation: str) -> "SoundnessReport":
        return SoundnessReport(ok=False, violations=self.violations + (violation,))

    def merge(self, other: "SoundnessReport") -> "SoundnessReport":
        violations = self.violations + other.violations
        return SoundnessReport(ok=not violations, violations=violations)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": list(self.violations)}


class HackClass(str, Enum):
    """How a proof got an undeserved acceptance."""

    #: An explicit "I did not finish" token the prover tolerates.
    PLACEHOLDER = "placeholder"
    #: A new assumption, which can simply be the goal.
    NEW_AXIOM = "new_axiom"
    #: The kernel was bypassed, disabled, or replaced by the compiler.
    KERNEL_BYPASS = "kernel_bypass"
    #: Resource limits removed, turning "unprovable" into "runs forever".
    RESOURCE_UNCAP = "resource_uncap"
    #: A term the statement depends on was redefined underneath it.
    DEFINITION_SHADOWING = "definition_shadowing"
    #: The required statement was altered, or a different one was proved.
    STATEMENT_TAMPERING = "statement_tampering"
    #: Elaborator settings that can silently weaken the statement.
    ELABORATION_TRICK = "elaboration_trick"
    #: Identifiers built from lookalike characters, to shadow invisibly.
    HOMOGLYPH = "homoglyph"


#: Classes that can make a *statement* mean something other than it
#: appears to. Deliberately excludes PLACEHOLDER: in this package a problem
#: file's `sorry` is the hole the participant fills, not a cheat. Also
#: excludes STATEMENT_TAMPERING, whose `variable` pattern is ordinary Lean
#: in a problem file, and RESOURCE_UNCAP, which is about proof search.
STATEMENT_HACK_CLASSES: tuple["HackClass", ...] = (
    HackClass.NEW_AXIOM,
    HackClass.KERNEL_BYPASS,
    HackClass.ELABORATION_TRICK,
    HackClass.HOMOGLYPH,
)

#: Everything, which is what a submitted proof is screened for.
PROOF_HACK_CLASSES: tuple["HackClass", ...] = tuple(HackClass)

# Frozen-target submissions do not restate or redefine the trusted target.
ANSWER_HACK_CLASSES: tuple[HackClass, ...] = tuple(
    kind for kind in HackClass
    if kind not in (HackClass.STATEMENT_TAMPERING, HackClass.DEFINITION_SHADOWING)
)


@dataclass(frozen=True)
class HackPattern:
    """One syntactic reward-hacking signature."""

    id: str
    hack_class: HackClass
    languages: tuple[str, ...]
    pattern: re.Pattern[str]
    #: Shown to the user. Says what was found and why it does not count.
    #: Carries one ``%s`` for the subject being screened, so the same
    #: pattern reads correctly whether a proof or a statement tripped it.
    message: str
    #: Lowercase literals, any one of which must appear for the regex to
    #: have a chance of matching. Checked with a substring test before the
    #: regex runs, which is a C-level scan instead of a backtracking one.
    #: **Must be a superset condition**: if the regex can match, at least
    #: one of these must be present, or the pattern would be skipped
    #: wrongly. ``test_prefilter_admits_every_pattern_example`` enforces
    #: that against ``example``, so an unsafe entry fails the suite.
    requires: tuple[str, ...] = ()
    #: A source snippet this pattern is meant to catch. Documents the
    #: trick and makes the table self-testing.
    example: str = ""

    def search(self, body: str, lowered: str | None = None) -> bool:
        """Whether this pattern fires, skipping the regex when it cannot."""
        if self.requires:
            haystack = lowered if lowered is not None else body.lower()
            if not any(literal in haystack for literal in self.requires):
                return False
        return bool(self.pattern.search(body))


def _p(
    id: str,
    hack_class: HackClass,
    languages: Sequence[str],
    regex: str,
    message: str,
    *,
    multiline: bool = False,
    requires: Sequence[str] = (),
    example: str = "",
) -> HackPattern:
    flags = re.MULTILINE if multiline else 0
    return HackPattern(
        id,
        hack_class,
        tuple(languages),
        re.compile(regex, flags),
        message,
        tuple(literal.lower() for literal in requires),
        example,
    )


_LEAN = ("lean4", "lean3")

#: Ordered only for readability; every pattern is evaluated.
#:
#: Each entry carries ``requires`` (cheap literal triggers, checked before
#: the regex -- see :class:`HackPattern`) and ``example`` (a snippet the
#: pattern must catch, which makes the table self-testing).
PATTERNS: tuple[HackPattern, ...] = (
    # -- placeholders -------------------------------------------------
    _p(
        "lean.sorry",
        HackClass.PLACEHOLDER,
        _LEAN,
        r"\bsorry\b",
        "%s contains `sorry`, which Lean accepts with only a warning",
        requires=("sorry",),
        example="theorem t : True := by sorry",
    ),
    _p(
        "lean.sorry_ax",
        HackClass.PLACEHOLDER,
        _LEAN,
        r"\bsorryAx\b",
        "%s cites `sorryAx` directly, the axiom behind `sorry`",
        requires=("sorryax",),
        example="theorem t : True := sorryAx _",
    ),
    _p(
        "lean.admit",
        HackClass.PLACEHOLDER,
        _LEAN,
        r"\badmit\b",
        "%s contains `admit`, which closes the goal without proving it",
        requires=("admit",),
        example="theorem t : True := by admit",
    ),
    _p(
        "coq.admitted",
        HackClass.PLACEHOLDER,
        ("coq",),
        r"\b(Admitted|admit|give_up)\b",
        "%s is `Admitted` rather than `Qed`, so nothing was proved",
        requires=("admit", "give_up"),
        example="Lemma t : True. Proof. Admitted.",
    ),
    _p(
        "isabelle.sorry",
        HackClass.PLACEHOLDER,
        ("isabelle",),
        r"\b(sorry|oops)\b",
        "%s contains `sorry`/`oops`, which abandons the goal",
        requires=("sorry", "oops"),
        example="lemma t: True sorry",
    ),
    _p(
        "generic.todo_placeholder",
        HackClass.PLACEHOLDER,
        ("axle",),
        r"\b(sorry|admit|TODO|FIXME|__PLACEHOLDER__)\b",
        "%s contains a placeholder token",
        requires=("sorry", "admit", "todo", "fixme", "__placeholder__"),
        example="proof: TODO",
    ),
    # -- new axioms ---------------------------------------------------
    _p(
        "lean.axiom",
        HackClass.NEW_AXIOM,
        _LEAN,
        r"^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|unsafe\s+)?axiom\b",
        "%s declares a new `axiom`, which can simply assert the goal",
        multiline=True,
        requires=("axiom",),
        example="axiom cheat (n : Nat) : n = n",
    ),
    _p(
        "lean.constant",
        HackClass.NEW_AXIOM,
        _LEAN,
        r"^\s*(?:@\[[^\]]*\]\s*)?constant\b",
        "%s declares a `constant`, which introduces an unproven term",
        multiline=True,
        requires=("constant",),
        example="constant cheat : True",
    ),
    _p(
        "lean.opaque",
        HackClass.NEW_AXIOM,
        ("lean4",),
        r"^\s*(?:@\[[^\]]*\]\s*)?opaque\b",
        "%s declares an `opaque` constant, whose value is assumed to exist",
        multiline=True,
        requires=("opaque",),
        example="opaque cheat : Nat",
    ),
    _p(
        "coq.assumption_decl",
        HackClass.NEW_AXIOM,
        ("coq",),
        r"^\s*(?:Axiom|Parameter|Hypothesis|Variable|Conjecture)\b",
        "%s declares an assumption instead of proving the goal",
        multiline=True,
        requires=("axiom", "parameter", "hypothesis", "variable", "conjecture"),
        example="Axiom cheat : True.",
    ),
    _p(
        "isabelle.axiomatization",
        HackClass.NEW_AXIOM,
        ("isabelle",),
        r"^\s*(?:axiomatization|consts)\b",
        "%s introduces an axiomatization instead of proving the goal",
        multiline=True,
        requires=("axiomatization", "consts"),
        example="axiomatization where cheat: True",
    ),
    # -- kernel bypass ------------------------------------------------
    _p(
        "lean.native_decide",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"\bnative_decide\b",
        "%s uses `native_decide`, which trusts the compiler instead of "
        "the kernel (it adds the `Lean.ofReduceBool` axiom)",
        requires=("native_decide",),
        example="theorem t : 2 + 2 = 4 := by native_decide",
    ),
    _p(
        "lean.skip_kernel_tc",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"set_option\s+debug\.skipKernelTC\s+true\b",
        "%s sets `debug.skipKernelTC`, turning off kernel typechecking",
        requires=("skipkerneltc",),
        example="set_option debug.skipKernelTC true",
    ),
    _p(
        "lean.implemented_by",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"@\[\s*(?:[^\]]*,\s*)?implemented_by\b",
        "%s uses `@[implemented_by]`, which swaps in an unverified "
        "implementation at runtime",
        requires=("implemented_by",),
        example="@[implemented_by fake] def f : Nat := 0",
    ),
    _p(
        "lean.extern",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"@\[\s*(?:[^\]]*,\s*)?extern\b",
        "%s uses `@[extern]`, delegating to unverified external code",
        requires=("extern",),
        example='@[extern "c_impl"] def f : Nat := 0',
    ),
    _p(
        "lean.unsafe",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"^\s*unsafe\b",
        "%s contains an `unsafe` declaration, which escapes the logic",
        multiline=True,
        requires=("unsafe",),
        example="unsafe def f : Nat := 0",
    ),
    _p(
        "lean.partial_def",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"^\s*partial\s+(?:unsafe\s+)?def\b",
        "%s declares a `partial def`, whose termination is not checked",
        multiline=True,
        requires=("partial",),
        example="partial def loop (n : Nat) : Nat := loop n",
    ),
    _p(
        "lean.trust_compiler",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"\b(Lean\.trustCompiler|Lean\.ofReduceBool|Lean\.ofReduceNat)\b",
        "%s cites a compiler-trust axiom directly",
        requires=("trustcompiler", "ofreducebool", "ofreducenat"),
        example="theorem t : True := Lean.ofReduceBool _ _ _",
    ),
    _p(
        "coq.guard_checking",
        HackClass.KERNEL_BYPASS,
        ("coq",),
        r"Unset\s+(Guard|Positivity|Universe)\s+Checking",
        "%s disables a Coq kernel check",
        requires=("unset",),
        example="Unset Guard Checking.",
    ),
    # -- resource uncapping -------------------------------------------
    _p(
        "lean.heartbeats_off",
        HackClass.RESOURCE_UNCAP,
        ("lean4",),
        r"set_option\s+maxHeartbeats\s+0\b",
        "%s removes the heartbeat limit entirely, so a non-terminating "
        "search is indistinguishable from a proof",
        requires=("maxheartbeats",),
        example="set_option maxHeartbeats 0",
    ),
    _p(
        "lean.binder_annotations_off",
        HackClass.ELABORATION_TRICK,
        ("lean4",),
        r"set_option\s+checkBinderAnnotations\s+false\b",
        "%s disables binder-annotation checking",
        requires=("checkbinderannotations",),
        example="set_option checkBinderAnnotations false",
    ),
    _p(
        "lean.auto_implicit",
        HackClass.ELABORATION_TRICK,
        ("lean4",),
        r"set_option\s+(?:relaxedAutoImplicit|autoImplicit)\s+true\b",
        "%s enables `autoImplicit`, which silently turns an unbound name "
        "into a universally quantified variable and can weaken the statement",
        requires=("autoimplicit",),
        example="set_option autoImplicit true",
    ),
    _p(
        "lean.structure_eta_off",
        HackClass.ELABORATION_TRICK,
        ("lean4",),
        r"set_option\s+(?:structureEta|backward\.\w+)\s+(?:false|true)\b",
        "%s changes a low-level elaborator setting that can alter what "
        "the statement means",
        requires=("structureeta", "backward."),
        example="set_option structureEta false",
    ),
    # -- truncating the file so the rest is never checked ---------------
    _p(
        "lean.hash_exit",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"^\s*#exit\b",
        "%s contains `#exit`, which makes Lean stop processing the rest of "
        "the file -- anything after it is never checked at all",
        multiline=True,
        requires=("#exit",),
        example="theorem t : True := trivial\n#exit\ngarbage",
    ),
    _p(
        "lean.proof_wanted",
        HackClass.PLACEHOLDER,
        ("lean4",),
        r"^\s*proof_wanted\b",
        "%s uses Mathlib's `proof_wanted`, which records a statement "
        "without proving it",
        multiline=True,
        requires=("proof_wanted",),
        example="proof_wanted t : True",
    ),
    # -- hypotheses smuggled in via section variables -------------------
    _p(
        "lean.variable_injection",
        HackClass.STATEMENT_TAMPERING,
        ("lean4",),
        r"^\s*variable[s]?\s*[({\[⦃]",
        "%s declares section `variable`s, which Lean silently adds to the "
        "theorem as extra hypotheses -- `variable (h : False)` weakens the "
        "statement to nothing while compiling cleanly",
        multiline=True,
        requires=("variable",),
        example="variable (hcheat : False)",
    ),
    # -- compile-time metaprogramming -----------------------------------
    _p(
        "lean.elab_metaprogramming",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        r"^\s*(?:@\[[^\]]*\]\s*)?(?:local\s+|scoped\s+)?(?:elab|elab_rules|run_cmd|run_elab|(?:builtin_)?initialize)\b",
        "%s runs compile-time metaprogramming, which can add declarations "
        "or axioms programmatically, out of reach of any text-level check",
        multiline=True,
        requires=("elab", "run_cmd", "run_elab", "initialize"),
        example="builtin_initialize IO.println \"ran at import\"",
    ),
    _p(
        "lean.compile_time_eval",
        HackClass.KERNEL_BYPASS,
        ("lean4",),
        # Not line-anchored: `open Foo in #eval ...` puts the command
        # mid-line, and flagging an honest proof that somehow contains the
        # text is the safe failure direction.
        r"#eval\b",
        "%s uses `#eval`, which runs arbitrary `IO` while the file "
        "elaborates -- that reaches the filesystem the grader stages its "
        "trusted modules in, which is outside what any proof check can see",
        requires=("#eval",),
        example="#eval IO.FS.writeFile \"x\" \"y\"",
    ),
)

#: Axioms a Lean 4 / Mathlib proof may legitimately depend on. Anything
#: else in a `#print axioms` listing is an assumption the model brought
#: with it, and `sorryAx` in particular means the proof is incomplete
#: however clean the compile looked.
LEGITIMATE_AXIOMS: frozenset[str] = frozenset(
    {
        "propext",
        "Classical.choice",
        "Quot.sound",
    }
)

#: Declaration keywords whose name could shadow something the statement uses.
_DECLARATION_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|local\s+|scoped\s+|noncomputable\s+|unsafe\s+|partial\s+)*"
    r"(def|abbrev|instance|structure|inductive|class|opaque|axiom|constant)\s+"
    r"([A-Za-z_À-￿][A-Za-z0-9_'À-￿.]*)",
    re.MULTILINE,
)

#: Syntax-level redefinitions, which have no name to compare but can
#: change what any statement means.
_SYNTAX_REDEF_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:local\s+|scoped\s+)?(notation|macro_rules|macro|syntax|infixl|infixr|infix|prefix|postfix)\b",
    re.MULTILINE,
)

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*")

#: Identifiers too common to treat as shadowing evidence on their own.
_COMMON_IDENTIFIERS = frozenset(
    {
        "theorem", "lemma", "example", "by", "fun", "let", "have", "show", "from",
        "if", "then", "else", "match", "with", "do", "at", "in", "and", "or", "not",
        "Type", "Prop", "Sort", "Nat", "Int", "Real", "Rat", "Complex", "Bool",
        "List", "Set", "Finset", "String", "Char", "Option", "Prod", "Sum", "Unit",
        "n", "m", "k", "i", "j", "x", "y", "z", "a", "b", "c", "p", "q", "r", "s", "t",
        "f", "g", "h", "hx", "hy", "hn", "hm", "hab", "ha", "hb", "hc",
    }
)

def label(hack_class: HackClass, pattern_id: str, message: str) -> str:
    """Format a violation as ``[class:pattern_id] message``.

    Every violation carries both tags so a report can count *which trick*
    was used, not only that something was wrong. "17 rejections" tells you
    nothing actionable; "14 x lean.sorry, 3 x lean.axiom" tells you the
    prompt needs to forbid placeholders.
    """
    return "[%s:%s] %s" % (hack_class.value, pattern_id, message)


def parse_label(violation: str) -> tuple[str | None, str | None]:
    """Recover ``(class, pattern_id)`` from a violation string."""
    match = _LABEL_RE.match(violation)
    if not match:
        return None, None
    return match.group(1), match.group(2)


_LABEL_RE = re.compile(r"^\[([a-z_]+):([a-zA-Z0-9_.]+)\]")


def declared_names(source: str, language: str) -> set[str]:
    """Names the source defines, which could shadow the statement's terms."""
    if language not in ("lean4", "lean3"):
        return set()
    return {m.group(2) for m in _DECLARATION_RE.finditer(source)}


def statement_identifiers(statement: str, language: str) -> set[str]:
    """Identifiers the statement depends on, minus the uninformative ones."""
    body = strip_comments(statement, language)
    # Drop the theorem's own name: redefining it is statement tampering,
    # caught by its own check, and it would otherwise fire on every proof.
    body = re.sub(r"^\s*(?:theorem|lemma|example)\s+\S+", " ", body)
    found = {m.group(0) for m in _IDENTIFIER_RE.finditer(body)}
    return {name for name in found if name not in _COMMON_IDENTIFIERS and len(name) > 1}


def _normalize_statement(text: str, language: str) -> str:
    """Collapse a statement to a form that ignores only harmless edits."""
    out = strip_comments(text, language)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r":=\s*(by\b.*)?$", "", out.strip()).strip()
    return out.rstrip(":= ").strip()


def _has_homoglyph_identifier(source: str) -> str | None:
    """Detect identifiers mixing ASCII with lookalike Unicode letters.

    ``Nаt`` with a Cyrillic а is a different name from ``Nat``, so a proof
    can define the lookalike, use it, and read as if it were about the
    real thing. Mathlib uses plenty of legitimate Unicode (``ε``, ``α``,
    ``ℝ``), so only a *mix* inside one identifier is suspicious.
    """
    suspicious = {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
        "х": "x", "у": "y", "і": "i", "ԁ": "d",
    }
    for match in re.finditer(r"[A-Za-z0-9_'Ѐ-ӿ]{2,}", source):
        token = match.group(0)
        hits = [ch for ch in token if ch in suspicious]
        if hits and any(ch.isascii() and ch.isalpha() for ch in token):
            return token
    return None


_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_.']*)", re.MULTILINE)
_OPEN_RE = re.compile(r"^\s*(?:local\s+|scoped\s+)?open\s+([^\n]+)", re.MULTILINE)

#: Module prefixes a Lean 4 proof may import without comment. Anything
#: else could be a module the model wrote, carrying its own axioms.
DEFAULT_ALLOWED_IMPORTS: tuple[str, ...] = ("Mathlib", "Std", "Batteries", "Init", "Aesop")


def _normalized_matches(pattern: HackPattern, body: str) -> Counter[str]:
    """What a pattern matched, whitespace-collapsed, **with multiplicity**.

    A multiset rather than a set, because several patterns match only a
    keyword and its opening bracket -- `lean.variable_injection` matches
    ``variable (`` and nothing more. Comparing distinct text alone would let
    an answer that inherits ``variable (N : Nat)`` add
    ``variable (h : False)`` for free, since both collapse to the same
    string. Counting catches it: two occurrences where the problem had one.

    Each occurrence is widened to its **whole logical line** -- the match
    plus any continuation lines, which are the ones starting with
    whitespace. The bare match is not enough: several patterns capture only
    a keyword and its opening bracket, so an answer that swapped an
    inherited ``variable (N : Nat)`` for ``variable (h : False)`` would look
    unchanged. Widening exposes the content; collapsing whitespace means
    re-wrapping an inherited line still looks inherited.
    """
    return Counter(_logical_line(body, m.start()) for m in pattern.pattern.finditer(body))


def _logical_line(body: str, start: int) -> str:
    """The line at ``start``, with its continuation lines, whitespace collapsed.

    Advances past leading whitespace first. Patterns anchored with
    ``^\\s*`` start their match on the newline *before* the keyword, so
    slicing straight from the match start returned the empty line before
    it -- which made every occurrence compare equal to every other.
    """
    while start < len(body) and body[start] in " \t\r\n":
        start += 1
    lines = body[start:].split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if line[:1] in (" ", "\t"):
            out.append(line)
        else:
            break
    return " ".join(" ".join(out).split())


def _introduced(pattern: HackPattern, body: str, baseline_body: str) -> bool:
    """Whether the subject added something the baseline did not have.

    True if any matched text occurs more often than in the baseline. False
    means every occurrence is accounted for by the problem file.

    An empty match set cannot happen here -- the caller has confirmed the
    pattern fires -- but returning True for it keeps the check conservative
    if a prefilter ever made it possible.
    """
    theirs = _normalized_matches(pattern, body)
    if not theirs:
        return True
    inherited = _normalized_matches(pattern, baseline_body)
    return any(count > inherited[text] for text, count in theirs.items())


def screen_source(
    source: str,
    language: str,
    *,
    required_statement: str | None = None,
    check_shadowing: bool = True,
    allowed_imports: Sequence[str] | None = DEFAULT_ALLOWED_IMPORTS,
    classes: Sequence[HackClass] = PROOF_HACK_CLASSES,
    subject: str = "proof",
    baseline: str | None = None,
) -> SoundnessReport:
    """Screen source text for the ways it can game a checker.

    ``classes`` restricts which kinds of hack are looked for, and
    ``subject`` is the noun the messages use. Both APIs screen Lean text,
    but not for the same things: a submitted proof is screened for
    everything (``PROOF_HACK_CLASSES``, the default), while a problem
    statement is screened for ``STATEMENT_HACK_CLASSES`` -- a problem
    file's `sorry` is the hole, not a cheat, and its `variable` bindings
    are ordinary Lean.

    ``required_statement`` enables the tampering and shadowing checks; pass
    ``None`` when the harness concatenated the statement itself and
    tampering is therefore impossible.

    ``allowed_imports`` lists module prefixes that may be imported; pass
    ``None`` to skip the check when your setup legitimately imports local
    modules.

    ``baseline`` is source the subject is allowed to have inherited -- the
    problem file, when screening an answer to it. A pattern that fires on
    text also present in the baseline is not reported, because the subject
    did not introduce it. This is what makes it safe for a problem to ship
    ``variable`` bindings, a ``set_option``, or an ``open``: a faithful copy
    is not tampering. The comparison is by *matched text*, so inheriting
    ``variable (N : Nat)`` does not excuse adding ``variable (h : False)``.
    """
    wanted_classes = frozenset(classes)
    baseline_body = strip_comments(baseline, language) if baseline else ""
    report = SoundnessReport()
    body = strip_comments(source, language)
    # Lowercased once and shared, so the literal prefilter in
    # HackPattern.search is a single C-level substring scan per pattern
    # instead of a backtracking regex over the whole body. On an honest
    # proof almost every pattern is ruled out this way.
    lowered = body.lower()

    for pattern in PATTERNS:
        if pattern.hack_class not in wanted_classes:
            continue
        if language not in pattern.languages or not pattern.search(body, lowered):
            continue
        if baseline_body and not _introduced(pattern, body, baseline_body):
            # Present in the problem too, so the answer inherited it.
            continue
        report = report.with_violation(
            label(pattern.hack_class, pattern.id, pattern.message % subject)
        )

    if (
        allowed_imports is not None
        and language == "lean4"
        and HackClass.NEW_AXIOM in wanted_classes
    ):
        for module in _IMPORT_RE.findall(body):
            root = module.split(".")[0]
            if root not in allowed_imports:
                report = report.with_violation(
                    label(
                        HackClass.NEW_AXIOM,
                        "lean.unknown_import",
                        "%s imports `%s`, which is not a known library module -- a "
                        "self-supplied module can carry its own axioms" % (subject, module),
                    )
                )

    homoglyph = (
        _has_homoglyph_identifier(body)
        if HackClass.HOMOGLYPH in wanted_classes
        else ""
    )
    if homoglyph:
        report = report.with_violation(
            label(
                HackClass.HOMOGLYPH,
                "generic.homoglyph_identifier",
                "identifier %r mixes ASCII with lookalike Unicode characters, which "
                "can shadow a real name invisibly" % homoglyph,
            )
        )

    if required_statement is not None and HackClass.STATEMENT_TAMPERING in wanted_classes:
        wanted = _normalize_statement(required_statement, language)
        if wanted and wanted not in _normalize_statement(source, language):
            report = report.with_violation(
                label(
                    HackClass.STATEMENT_TAMPERING,
                    "generic.statement_altered",
                    "%s does not contain the required statement "
                    "verbatim, so it may prove something else" % subject,
                )
            )

        if check_shadowing:
            shadowed = declared_names(body, language) & statement_identifiers(
                required_statement, language
            )
            if shadowed:
                report = report.with_violation(
                    label(
                        HackClass.DEFINITION_SHADOWING,
                        "lean.redefines_statement_term",
                        "%s redefines %s, which the statement depends on -- the "
                        "statement may no longer mean what it says"
                        % (subject, ", ".join(sorted(shadowed)[:4])),
                    )
                )
            if _SYNTAX_REDEF_RE.search(body):
                report = report.with_violation(
                    label(
                        HackClass.DEFINITION_SHADOWING,
                        "lean.notation_redefinition",
                        "%s introduces new notation or macro rules, which can change "
                        "what the statement means without editing its text" % subject,
                    )
                )

            # `open Foo` can make a name in the statement resolve to a
            # different definition, with the statement's text untouched.
            wanted_names = statement_identifiers(required_statement, language)
            bare_names = {n.split(".")[-1] for n in wanted_names} | wanted_names
            for opened in _OPEN_RE.findall(body):
                namespaces = re.findall(r"[A-Za-z_][A-Za-z0-9_.']*", opened.split(" in ")[0])
                hiding = set(re.findall(r"hiding\s+([^\n]+)", opened))
                for namespace in namespaces:
                    if namespace in ("hiding", "renaming", "in"):
                        continue
                    if namespace.split(".")[-1] in bare_names and not hiding:
                        report = report.with_violation(
                            label(
                                HackClass.DEFINITION_SHADOWING,
                                "lean.open_shadows_statement",
                                "%s opens namespace `%s`, which shares a name with "
                                "something the statement uses and can redirect it to a "
                                "different definition" % (subject, namespace),
                            )
                        )

    return report


def audit_axioms(
    axioms: Iterable[str],
    *,
    allowed: frozenset[str] = LEGITIMATE_AXIOMS,
) -> SoundnessReport:
    """Judge a ``#print axioms`` listing.

    This is the strongest check available, and the only one that sees
    through indirection: a ``sorry`` inside a helper lemma, or an axiom
    imported from another file, still shows up here. Whatever the regexes
    above did or did not catch, a proof whose axiom set is clean is
    kernel-checked, and one containing ``sorryAx`` is not a proof.
    """
    report = SoundnessReport()
    for axiom in sorted(set(axioms) - allowed):
        if axiom == "sorryAx":
            report = report.with_violation(
                label(
                    HackClass.PLACEHOLDER,
                    "kernel.sorry_ax",
                    "kernel axiom audit: the proof depends on `sorryAx`, so it is "
                    "incomplete however cleanly it compiled",
                )
            )
        elif axiom in ("Lean.ofReduceBool", "Lean.ofReduceNat", "Lean.trustCompiler"):
            report = report.with_violation(
                label(
                    HackClass.KERNEL_BYPASS,
                    "kernel.compiler_trust",
                    "kernel axiom audit: the proof depends on `%s`, so it is trusted "
                    "from the compiler rather than checked by the kernel" % axiom,
                )
            )
        else:
            report = report.with_violation(
                label(
                    HackClass.NEW_AXIOM,
                    "kernel.nonstandard_axiom",
                    "kernel axiom audit: the proof depends on the non-standard axiom "
                    "`%s` -- declaring an axiom passes the kernel with no complaint at "
                    "all, so this listing is the only place it shows up" % axiom,
                )
            )
    return report
