"""Does ``answer.lean`` prove ``theorem.lean``?

The input contract: two Lean files, one theorem per problem file. The
question is **what the answer proves**, not whether it compiles -- a file
that compiles perfectly can have quietly restated the theorem.

Answered in two stages, and the order matters:

1. **Text screen** (:func:`match_submission`) -- fast, needs no prover.
   Catches a different theorem, a weakened hypothesis, a gutted
   definition, an unproved helper, reward hacking.
2. **Kernel confirmation** (:meth:`SubmissionMatcher.confirm`) -- states
   the *theorem's* proposition and closes it with the *answer's* proof
   term. If that typechecks, the answer proves the theorem, whatever the
   two texts look like. When the stages disagree the kernel wins.

Four properties of the format drive the design, and each breaks the naive
answer:

1. **``theorem.lean`` introduces definitions.** It is not just a
   statement: the ``abbrev``s and ``def``s the statement is written in
   terms of are the problem's *vocabulary*. An answer that redefines one
   has changed the problem while leaving the theorem's text byte-identical
   -- so the theorem file is the authority on them, including when
   building the confirmation probe.
2. **``answer.lean`` may introduce many more.** A real proof needs its own
   machinery, and none of it is penalised. What grows with it is the
   number of places to hide an unproved assumption, so a helper the target
   cites but leaves ``sorry``-backed is fatal.
3. **``sorry`` in the theorem file is legitimate.** An open conjecture is
   *stated* that way, so it is a property of the problem, not a violation.
   In the answer, once a proof is claimed, it is not.
4. **``answer(sorry)`` is a hole the answer is supposed to fill.**
   ``theorem H : chromaticNumber = answer(sorry)`` becomes ``answer(4)``,
   so the statement *legitimately changes* and verbatim comparison would
   flag every solved problem. What must not change is everything else, and
   where the holes are.

So the text comparison is: signatures equal after normalizing away
``answer(...)`` contents and whitespace, and nothing else. What it cannot
decide is semantic equivalence -- an answer that restates the goal in a
provably equivalent but textually different way is flagged. That is the
deliberate direction, since a flag is reviewable and a missed substitution
is a wrong number in a paper, and stage 2 exists to settle exactly those
cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

import dataclasses

from ..comments import strip_comments
from ..soundness import HackClass, parse_label, screen_source
from ..types import StatementTask, Status
from .lean_file import (
    LeanDeclaration,
    LeanFile,
    extract_answer_arguments,
    normalize_signature,
    parse_lean_file,
)

__all__ = [
    "MatchStatus",
    "Mismatch",
    "MismatchKind",
    "MatchReport",
    "match_submission",
    "SubmissionMatcher",
]


class DefinitionStatus(str, Enum):
    """What the answer did with each definition the theorem file introduced.

    ``theorem.lean`` is not just a statement: it introduces the ``abbrev``s,
    ``def``s and instances the statement is written in terms of. Those are
    the *vocabulary* of the problem, and an answer that changes one has
    changed the problem while leaving the theorem's text untouched. So each
    one is classified rather than lumped together.
    """

    #: Identical to the theorem file's. The normal case for a self-contained
    #: answer file.
    UNCHANGED = "unchanged"
    #: Present but different. The theorem now means something else.
    CHANGED = "changed"
    #: The theorem file defines it; the answer does not. The answer may be
    #: relying on a same-named definition from the library, which is not the
    #: same definition.
    MISSING = "missing"
    #: New in the answer, colliding with nothing. Helper definitions for the
    #: proof are legitimate.
    ADDED = "added"


@dataclass(frozen=True)
class DefinitionDiff:
    """One definition, compared between theorem file and answer."""

    name: str
    status: DefinitionStatus
    kind: str = ""
    #: True when the theorem's statement actually mentions this name, i.e.
    #: when changing it changes what is being claimed.
    load_bearing: bool = False
    expected: str = ""
    actual: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "kind": self.kind,
            "load_bearing": self.load_bearing,
            "expected": self.expected[:300],
            "actual": self.actual[:300],
        }


class MismatchKind(str, Enum):
    """What specifically differs between the theorem file and the answer."""

    #: The target declaration is absent from the answer.
    TARGET_MISSING = "target_missing"
    #: Present, but the statement is not the theorem's statement.
    STATEMENT_CHANGED = "statement_changed"
    #: The answer added or removed an ``answer(...)`` hole.
    ANSWER_HOLES_CHANGED = "answer_holes_changed"
    #: An ``answer(...)`` hole is still ``sorry``: no answer was given.
    ANSWER_NOT_FILLED = "answer_not_filled"
    #: The target's proof is still a placeholder.
    PROOF_MISSING = "proof_missing"
    #: A definition the statement depends on was changed.
    DEPENDENCY_CHANGED = "dependency_changed"
    #: A definition the statement depends on is gone.
    DEPENDENCY_MISSING = "dependency_missing"
    #: The answer dropped an import the theorem file had.
    IMPORT_REMOVED = "import_removed"
    #: Reward hacking in the answer.
    REWARD_HACKING = "reward_hacking"
    #: The declaration changed kind, e.g. theorem to abbrev.
    KIND_CHANGED = "kind_changed"
    #: The problem file holds several statements, against the one-per-file
    #: convention, so which theorem is under test had to be inferred.
    AMBIGUOUS_TARGET = "ambiguous_target"
    #: The answer added statements the theorem file did not have. Helper
    #: lemmas are legitimate, so this is a note, not a failure.
    EXTRA_STATEMENTS = "extra_statements"
    #: A helper statement in the answer is left unproved. Fatal when the
    #: target's proof cites it, since the proof is then conditional on an
    #: assumption; otherwise dead weight worth reporting.
    UNPROVED_HELPER = "unproved_helper"


@dataclass(frozen=True)
class Mismatch:
    """One difference, with enough detail to adjudicate it by hand."""

    kind: MismatchKind
    detail: str
    #: True when this alone means the answer does not prove the theorem.
    fatal: bool = True
    expected: str = ""
    actual: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "detail": self.detail,
            "fatal": self.fatal,
            "expected": self.expected[:400],
            "actual": self.actual[:400],
        }


class MatchStatus(str, Enum):
    """Whether the answer proves the theorem."""

    #: Same statement, answer filled where required, proof supplied.
    MATCHED = "matched"
    #: Same statement, but the proof is still a placeholder. An honest
    #: non-answer, not a mismatch.
    MATCHED_BUT_UNPROVED = "matched_but_unproved"
    #: The statement is not the problem's statement.
    MISMATCHED = "mismatched"
    #: The target is not in the answer at all.
    MISSING = "missing"
    #: Neither file could be parsed well enough to compare.
    UNPARSED = "unparsed"

    @property
    def answers_the_problem(self) -> bool:
        return self is MatchStatus.MATCHED


@dataclass
class MatchReport:
    """The verdict on one answer file."""

    target: str
    status: MatchStatus
    mismatches: tuple[Mismatch, ...] = ()
    #: What the answer put in each ``answer(...)`` hole, in order.
    answers: tuple[str, ...] = ()
    #: Normalized signatures, kept so a disputed verdict can be inspected.
    expected_signature: str = ""
    actual_signature: str = ""
    problem_declarations: int = 0
    submission_declarations: int = 0
    #: The kernel's answer to "does the answer's proof close the *theorem's*
    #: proposition?". ``True`` means the prover confirmed it, ``False`` means
    #: the prover rejected it, ``None`` means the check did not run -- which
    #: must never be read as either.
    proves_the_theorem: bool | None = None
    #: How the confirmation came out, for the report.
    confirmation_detail: str = ""
    #: Every definition, classified: kept, changed, missing, or added by the
    #: answer. An answer may add as many as it likes; what it may not do is
    #: change the theorem file's.
    definitions: tuple[DefinitionDiff, ...] = ()

    @property
    def fatal_mismatches(self) -> tuple[Mismatch, ...]:
        return tuple(m for m in self.mismatches if m.fatal)

    @property
    def verdict(self) -> str:
        """The overall answer, with the kernel outranking the text screen.

        The two can disagree, and when they do the prover is right:

        * **text mismatched, kernel confirmed** -- the answer restated the
          goal in a provably equivalent way. Accepted; the text screen was
          being conservative, which is its job.
        * **text matched, kernel rejected** -- something subtler is wrong
          than the text shows. Not accepted.
        * **kernel not run** -- the text screen is all there is, so the
          verdict says so rather than implying the proof was checked.
        """
        if self.proves_the_theorem is True:
            return "proves_the_theorem"
        if self.proves_the_theorem is False:
            return "does_not_prove_the_theorem"
        if self.status is MatchStatus.MATCHED:
            return "statement_matches_unverified_proof"
        return self.status.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "status": self.status.value,
            "answers_the_problem": self.status.answers_the_problem,
            "answers": list(self.answers),
            "mismatches": [m.to_dict() for m in self.mismatches],
            "expected_signature": self.expected_signature,
            "actual_signature": self.actual_signature,
            "problem_declarations": self.problem_declarations,
            "submission_declarations": self.submission_declarations,
            "proves_the_theorem": self.proves_the_theorem,
            "confirmation_detail": self.confirmation_detail,
            "verdict": self.verdict,
            "definitions": [d.to_dict() for d in self.definitions],
            "definition_counts": self.definition_counts,
        }

    @property
    def definition_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for diff in self.definitions:
            counts[diff.status.value] = counts.get(diff.status.value, 0) + 1
        return counts

    def format_text(self) -> str:
        lines = ["target:  %s" % self.target, "verdict: %s" % self.verdict]
        if self.proves_the_theorem is None:
            lines.append(
                "  (text screen only -- the prover was not asked whether the answer's "
                "proof closes the theorem's proposition)"
            )
        else:
            lines.append(
                "  kernel: %s -- %s"
                % (
                    "CONFIRMED" if self.proves_the_theorem else "REJECTED",
                    self.confirmation_detail,
                )
            )
        lines.append("text screen: %s" % self.status.value)
        if self.answers:
            lines.append("answer(s) submitted: %s" % ", ".join(self.answers))
        counts = self.definition_counts
        if counts:
            lines.append(
                "definitions: "
                + ", ".join("%s=%d" % kv for kv in sorted(counts.items()))
                + ("  (added ones are the answer's own and are not penalised)"
                   if counts.get("added") else "")
            )
        for mismatch in self.mismatches:
            lines.append(
                "  %s %-24s %s"
                % ("FATAL" if mismatch.fatal else "note ", mismatch.kind.value, mismatch.detail)
            )
            if mismatch.expected and mismatch.actual:
                lines.append("    expected: %s" % mismatch.expected[:160])
                lines.append("    actual:   %s" % mismatch.actual[:160])
        if self.status is MatchStatus.MISMATCHED:
            lines.append(
                "  The answer may compile perfectly on its own; what it does not do is "
                "state the theorem that was set."
            )
        return "\n".join(lines)


def _dependency_names(declaration: LeanDeclaration, candidates: Sequence[str]) -> set[str]:
    """Which of ``candidates`` the declaration's signature mentions."""
    body = strip_comments(declaration.signature, "lean4")
    return {name for name in candidates if name and _mentions(body, name)}


def _mentions(text: str, name: str) -> bool:
    import re

    return re.search(r"(?<![\w.])%s(?![\w])" % re.escape(name), text) is not None


def match_submission(
    problem_source: str,
    submission_source: str,
    *,
    target: str | None = None,
    require_proof: bool = True,
    check_dependencies: bool = True,
    check_reward_hacking: bool = True,
) -> MatchReport:
    """Compare a submitted Lean file against the problem's Lean file.

    The expected layout is **one problem per file**, so ``target`` is
    normally left out and the file's single theorem is used. It is still
    inferred safely when a file carries helper ``abbrev``s alongside the
    theorem, and a file that turns out to hold several statements is
    reported rather than silently guessed at -- that is a dataset error,
    and picking the wrong theorem would make every later verdict meaningless.
    ``target`` remains available to name one explicitly.
    """
    problem = parse_lean_file(problem_source)
    submission = parse_lean_file(submission_source)

    target_declaration = _pick_target(problem, target)
    if target_declaration is None:
        return MatchReport(
            target=target or "?",
            status=MatchStatus.UNPARSED,
            mismatches=(
                Mismatch(
                    MismatchKind.TARGET_MISSING,
                    "could not find %s in the problem file"
                    % ("%r" % target if target else "any theorem"),
                ),
            ),
            problem_declarations=len(problem.declarations),
            submission_declarations=len(submission.declarations),
        )

    name = target_declaration.name
    submitted = submission.by_name().get(name)
    mismatches: list[Mismatch] = []

    # The convention is one problem per file. Verify it rather than assume
    # it: if a problem file holds several theorems and no target was named,
    # inference picked one and every verdict below is about that one.
    problem_statements = problem.statements()
    if target is None and len(problem_statements) > 1:
        mismatches.append(
            Mismatch(
                MismatchKind.AMBIGUOUS_TARGET,
                "the theorem file declares %d statements (%s) but the convention is one "
                "per file; %r was used as the target. Pass an explicit target to be sure."
                % (
                    len(problem_statements),
                    ", ".join(d.name for d in problem_statements[:4]),
                    name,
                ),
                fatal=False,
            )
        )

    if submitted is None:
        return MatchReport(
            target=name,
            status=MatchStatus.MISSING,
            mismatches=(
                Mismatch(
                    MismatchKind.TARGET_MISSING,
                    "the answer has no declaration named %r; it has %s"
                    % (
                        name,
                        ", ".join(d.name for d in submission.statements()[:6]) or "no statements",
                    ),
                ),
            ),
            expected_signature=normalize_signature(target_declaration.signature),
            problem_declarations=len(problem.declarations),
            submission_declarations=len(submission.declarations),
        )

    expected = normalize_signature(target_declaration.signature)
    actual = normalize_signature(submitted.signature)
    answers = extract_answer_arguments(submitted.signature)

    if submitted.kind != target_declaration.kind:
        mismatches.append(
            Mismatch(
                MismatchKind.KIND_CHANGED,
                "the theorem file declares a %s, the answer a %s"
                % (target_declaration.kind, submitted.kind),
                expected=target_declaration.kind,
                actual=submitted.kind,
            )
        )

    if expected != actual:
        mismatches.append(
            Mismatch(
                MismatchKind.STATEMENT_CHANGED,
                "the answer's statement is not the theorem's statement "
                "(compared with answer(...) holes and whitespace normalized away)",
                expected=expected,
                actual=actual,
            )
        )

    if target_declaration.answer_holes != submitted.answer_holes:
        mismatches.append(
            Mismatch(
                MismatchKind.ANSWER_HOLES_CHANGED,
                "the theorem has %d answer(...) hole(s), the answer %d"
                % (target_declaration.answer_holes, submitted.answer_holes),
            )
        )
    elif submitted.is_open_hole:
        mismatches.append(
            Mismatch(
                MismatchKind.ANSWER_NOT_FILLED,
                "an answer(...) hole is still `sorry`, so no answer was actually given",
            )
        )

    if require_proof and submitted.is_unproved:
        mismatches.append(
            Mismatch(
                MismatchKind.PROOF_MISSING,
                "the target's proof is still a placeholder (%s)"
                % strip_comments(submitted.body, "lean4").strip()[:40],
                # Not fatal to *matching*: the statement may be right and
                # the proof simply absent, which is an honest non-answer
                # and a different outcome from a changed statement.
                fatal=False,
            )
        )

    definition_diffs: tuple[DefinitionDiff, ...] = ()
    if check_dependencies:
        dependency_mismatches, definition_diffs = _check_dependencies(
            problem, submission, target_declaration
        )
        mismatches.extend(dependency_mismatches)

    missing_imports = set(problem.imports()) - set(submission.imports())
    if missing_imports:
        mismatches.append(
            Mismatch(
                MismatchKind.IMPORT_REMOVED,
                "the answer dropped import(s) the theorem file had: %s"
                % ", ".join(sorted(missing_imports)),
                fatal=False,
            )
        )

    extra = {d.name for d in submission.statements()} - {d.name for d in problem_statements}
    if extra:
        # Worth surfacing without penalising: a submission may factor its
        # argument through helper lemmas, which is good practice, and the
        # same shape is also where a fabricated lemma would hide -- so it is
        # reported for review rather than judged either way.
        mismatches.append(
            Mismatch(
                MismatchKind.EXTRA_STATEMENTS,
                "the answer adds %d statement(s) the theorem file did not have (%s); "
                "helper lemmas are legitimate, but they are also where an unproved "
                "assumption would hide, so check them"
                % (len(extra), ", ".join(sorted(extra)[:4])),
                fatal=False,
            )
        )

    if check_reward_hacking:
        mismatches.extend(_screen_submission(submission, submitted))

    status = _decide(mismatches, submitted)
    return MatchReport(
        target=name,
        status=status,
        mismatches=tuple(mismatches),
        answers=answers,
        expected_signature=expected,
        actual_signature=actual,
        problem_declarations=len(problem.declarations),
        submission_declarations=len(submission.declarations),
        definitions=definition_diffs,
    )


def _with_confirmation(
    report: MatchReport, proves: bool | None, detail: str
) -> MatchReport:
    return dataclasses.replace(
        report, proves_the_theorem=proves, confirmation_detail=detail
    )


def _pick_target(problem: LeanFile, target: str | None) -> LeanDeclaration | None:
    by_name = problem.by_name()
    if target:
        return by_name.get(target)
    statements = problem.statements()
    if not statements:
        return None
    for declaration in statements:
        if "research" in declaration.categories:
            return declaration
    return statements[0]


def _definition_text(declaration: LeanDeclaration) -> str:
    """Normalized form of a definition, signature and body together.

    The body is part of a definition's identity in a way it is not for a
    theorem: two ``abbrev``s with the same signature and different bodies
    are different predicates, and substituting one for the other is exactly
    the subversion this is here to catch.
    """
    return normalize_signature(declaration.signature + " := " + declaration.body)


def diff_definitions(
    problem: LeanFile, submission: LeanFile, target: LeanDeclaration | None = None
) -> tuple[DefinitionDiff, ...]:
    """Classify every definition across the two files.

    ``load_bearing`` marks the ones the target's statement actually mentions.
    That distinction matters for severity: changing a definition the
    statement names changes the claim, while changing an unrelated helper
    only affects whether the proof works.
    """
    problem_definitions = {d.name: d for d in problem.definitions()}
    answer_definitions = {d.name: d for d in submission.definitions()}
    used = (
        _dependency_names(target, list(problem_definitions)) if target is not None else set()
    )

    out: list[DefinitionDiff] = []
    for name in sorted(problem_definitions):
        original = problem_definitions[name]
        resubmitted = answer_definitions.get(name)
        load_bearing = name in used
        if resubmitted is None:
            out.append(
                DefinitionDiff(
                    name, DefinitionStatus.MISSING, original.kind, load_bearing,
                    expected=_definition_text(original),
                )
            )
            continue
        before, after = _definition_text(original), _definition_text(resubmitted)
        out.append(
            DefinitionDiff(
                name,
                DefinitionStatus.UNCHANGED if before == after else DefinitionStatus.CHANGED,
                original.kind,
                load_bearing,
                expected=before,
                actual=after,
            )
        )
    for name in sorted(set(answer_definitions) - set(problem_definitions)):
        out.append(
            DefinitionDiff(
                name,
                DefinitionStatus.ADDED,
                answer_definitions[name].kind,
                load_bearing=False,
                actual=_definition_text(answer_definitions[name]),
            )
        )
    return tuple(out)


def _check_dependencies(
    problem: LeanFile, submission: LeanFile, target: LeanDeclaration
) -> tuple[list[Mismatch], tuple[DefinitionDiff, ...]]:
    """Turn the definition diff into mismatches, weighted by load-bearing.

    Redefining ``IsSumDistinctSet`` leaves the theorem's text byte-identical
    and changes what it claims, so this is where that is caught.
    """
    diffs = diff_definitions(problem, submission, target)
    out: list[Mismatch] = []

    for diff in diffs:
        if diff.status is DefinitionStatus.CHANGED:
            out.append(
                Mismatch(
                    MismatchKind.DEPENDENCY_CHANGED,
                    "%s %s is defined differently in the answer, so the theorem %s"
                    % (
                        diff.kind,
                        diff.name,
                        "no longer means what it says"
                        if diff.load_bearing
                        else "may rest on a different helper",
                    ),
                    # Only fatal when the statement actually names it; an
                    # unrelated helper differing is the answer's business.
                    fatal=diff.load_bearing,
                    expected=diff.expected,
                    actual=diff.actual,
                )
            )
        elif diff.status is DefinitionStatus.MISSING and diff.load_bearing:
            out.append(
                Mismatch(
                    MismatchKind.DEPENDENCY_MISSING,
                    "the theorem's statement depends on %s %s, which the answer does not "
                    "define -- so the name resolves to something else, or not at all"
                    % (diff.kind, diff.name),
                )
            )

    # Notation and macros can redirect a name in the statement without
    # touching its text, which is why they are checked here where both files
    # are in hand rather than left to the single-file screen.
    if _introduces_syntax(submission) and not _introduces_syntax(problem):
        out.append(
            Mismatch(
                MismatchKind.DEPENDENCY_CHANGED,
                "the answer introduces notation or macro rules that the theorem file "
                "does not, which can change how the statement parses without editing it",
            )
        )
    return out, diffs


_SYNTAX_DECL_RE = None


def _introduces_syntax(parsed: LeanFile) -> bool:
    import re

    global _SYNTAX_DECL_RE
    if _SYNTAX_DECL_RE is None:
        _SYNTAX_DECL_RE = re.compile(
            r"^\s*(?:local\s+|scoped\s+)?(?:notation|macro_rules|macro|syntax|infixl|"
            r"infixr|infix|prefix|postfix)\b",
            re.MULTILINE,
        )
    return bool(_SYNTAX_DECL_RE.search(strip_comments(parsed.source, "lean4")))


def _screen_submission(
    submission: LeanFile, target: LeanDeclaration
) -> list[Mismatch]:
    """Screen the answer for reward hacking, at the right scope for each class.

    An answer file may legitimately be much larger than the theorem file --
    a real proof often needs many auxiliary definitions and lemmas. That is
    fine, and none of it is penalised. What it does mean is that there are
    many more places to hide an unproved assumption, so the scoping has to
    be right:

    * **Anything but placeholders is file-scoped.** An ``axiom`` declared
      anywhere in the answer can be cited by the proof, so screening only
      the target's own block would miss it.
    * **Placeholders are file-scoped too, once a proof is claimed.** A
      helper lemma closed with ``sorry`` is an assumption, and the target
      citing it makes the whole proof conditional on it. The only exception
      is the target itself being unproved, which is an honest non-answer
      already reported as ``PROOF_MISSING`` -- calling that reward hacking
      would turn "did not solve it" into "cheated".
    """
    out: list[Mismatch] = []

    file_level = screen_source(
        submission.source, "lean4", required_statement=None, allowed_imports=None
    )
    non_placeholder = [
        violation
        for violation in file_level.violations
        if parse_label(violation)[0] != HackClass.PLACEHOLDER.value
    ]
    if non_placeholder:
        out.append(Mismatch(MismatchKind.REWARD_HACKING, "; ".join(non_placeholder)))

    if target.is_unproved:
        return out

    # A proof is claimed, so every placeholder in the file is in scope.
    unproved_helpers = [
        d
        for d in submission.declarations
        if d.name != target.name and d.is_unproved and d.is_statement
    ]
    if unproved_helpers:
        cited = [d.name for d in unproved_helpers if _mentions(target.body, d.name)]
        out.append(
            Mismatch(
                MismatchKind.UNPROVED_HELPER,
                "the answer leaves %d helper statement(s) unproved (%s)%s"
                % (
                    len(unproved_helpers),
                    ", ".join(d.name for d in unproved_helpers[:4]),
                    "; the target's proof cites %s, so the proof is conditional on "
                    "an assumption" % ", ".join(cited[:3])
                    if cited
                    else ", though the target's proof does not cite them",
                ),
                # Fatal when cited: the target is then proved only modulo an
                # unproved lemma. Otherwise dead weight worth reporting.
                fatal=bool(cited),
            )
        )

    placeholders = [
        violation
        for violation in file_level.violations
        if parse_label(violation)[0] == HackClass.PLACEHOLDER.value
    ]
    if placeholders and not unproved_helpers:
        # A placeholder that is not a whole unproved helper: a `sorry`
        # inside an otherwise-real proof.
        out.append(Mismatch(MismatchKind.REWARD_HACKING, "; ".join(placeholders)))
    return out


def _decide(mismatches: Sequence[Mismatch], submitted: LeanDeclaration) -> MatchStatus:
    if any(m.fatal for m in mismatches):
        return MatchStatus.MISMATCHED
    if any(m.kind is MismatchKind.PROOF_MISSING for m in mismatches):
        return MatchStatus.MATCHED_BUT_UNPROVED
    return MatchStatus.MATCHED


class SubmissionMatcher:
    """Answers "does this answer prove this theorem?" -- with the prover.

    Two stages, and the order is the point:

    1. **Text screen** (:func:`match_submission`) -- fast, needs no prover,
       and catches the blatant cases: a different theorem, a weakened
       hypothesis, a gutted definition, reward hacking.
    2. **Kernel confirmation** (:meth:`confirm`) -- states the *theorem's*
       proposition and closes it with the *answer's* proof term. If that
       typechecks, the answer proves the theorem, whatever the two texts
       look like.

    Stage 2 is the one that actually answers the question, so when the two
    disagree the kernel wins. A text mismatch that the kernel confirms is a
    provably equivalent restatement and is accepted; a text match the kernel
    rejects is not.
    """

    def __init__(self, verifier: Any = None, *, timeout_s: float = 180.0) -> None:
        self.verifier = verifier
        self.timeout_s = timeout_s

    def match(
        self, problem_source: str, submission_source: str, *, target: str | None = None, **kw: Any
    ) -> MatchReport:
        """Text screen only. Use :meth:`check` to also ask the prover."""
        return match_submission(problem_source, submission_source, target=target, **kw)

    def check(
        self,
        problem_source: str,
        submission_source: str,
        *,
        target: str | None = None,
        **kw: Any,
    ) -> MatchReport:
        """Text screen, then kernel confirmation when a prover is available."""
        report = self.match(problem_source, submission_source, target=target, **kw)
        if self.verifier is None:
            return report
        return self.confirm(report, problem_source, submission_source, target=target)

    def confirm(
        self,
        report: MatchReport,
        problem_source: str,
        submission_source: str,
        *,
        target: str | None = None,
    ) -> MatchReport:
        """Ask the prover whether the answer's proof closes the theorem.

        Never raises, and never upgrades a ``None`` verdict into a claim:
        if the probe cannot be built or the prover cannot run, the report
        comes back with ``proves_the_theorem=None`` and a reason.
        """
        if self.verifier is None:
            return _with_confirmation(report, None, "no verifier configured")

        source = self.build_confirmation_source(
            problem_source, submission_source, target=target
        )
        if source is None:
            return _with_confirmation(
                report,
                None,
                "could not assemble the probe (no proof term to test, or the "
                "answer(...) holes do not line up)",
            )

        statement_task = StatementTask(
            task_id=report.target or "match_probe",
            informal_statement="",
            formal_statement="theorem ftp_eval_match_probe : True",
            header="",
            language=getattr(self.verifier, "language", "lean4"),
        )
        result = self.verifier.probe(statement_task, source, timeout_s=self.timeout_s)

        if result.status is Status.VERIFIED:
            return _with_confirmation(
                report,
                True,
                "the theorem's proposition typechecks against the answer's proof term",
            )
        if result.status in (Status.ERROR, Status.SKIPPED):
            return _with_confirmation(
                report, None, "the probe could not run (%s)" % result.status.value
            )
        first = next((d.message for d in result.diagnostics if d.message), "")
        return _with_confirmation(
            report,
            False,
            "the answer's proof does not close the theorem's proposition (%s)"
            % (first[:160] or result.status.value),
        )

    def build_confirmation_source(
        self, problem_source: str, submission_source: str, *, target: str | None = None
    ) -> str | None:
        """Build a file that settles the match by typechecking.

        The idea: state the **problem's** theorem, and prove it with the
        **answer's** proof term. If that compiles, the answer
        proves exactly what was asked, whatever the two texts look like --
        which resolves the case textual comparison cannot, a restatement
        that is provably equivalent but written differently.

        ``answer(...)`` holes in the problem are filled from the
        submission first, since an unfilled hole cannot be typechecked.
        Returns ``None`` when the pieces could not be assembled, which the
        caller must read as "not confirmed", never as "confirmed".
        """
        problem = parse_lean_file(problem_source)
        submission = parse_lean_file(submission_source)
        original = _pick_target(problem, target)
        if original is None:
            return None
        submitted = submission.by_name().get(original.name)
        if submitted is None or submitted.is_unproved:
            return None

        statement = original.signature
        problem_answers = extract_answer_arguments(statement)
        submitted_answers = extract_answer_arguments(submitted.signature)
        if len(problem_answers) != len(submitted_answers):
            return None
        for before, after in zip(problem_answers, submitted_answers, strict=True):
            statement = statement.replace("answer(%s)" % before, "answer(%s)" % after, 1)

        # The theorem file is the authority on the problem's vocabulary, so
        # its definitions go in -- NOT the answer's. Using the answer's would
        # defeat the whole check: an answer that replaces
        # `abbrev IsSumDistinctSet ... := True` would be compiled against its
        # own gutted definition and confirm against a claim nobody asked.
        problem_names = {d.name for d in problem.definitions()}
        context = [d.source for d in problem.definitions()]

        # The answer's *additional* definitions and helper lemmas do come
        # along -- its proof may cite them -- but only the ones that collide
        # with nothing in the problem, so they cannot shadow the vocabulary.
        extras = [
            d.source
            for d in submission.declarations
            if d.name not in problem_names
            and d.name != original.name
            and not d.is_unproved
        ]

        parts = [
            problem.preamble,
            *context,
            *extras,
            "theorem ftp_eval_match_probe %s := %s" % (statement, submitted.body),
            problem.epilogue or submission.epilogue,
        ]
        return "\n\n".join(part for part in parts if part and part.strip()) + "\n"
