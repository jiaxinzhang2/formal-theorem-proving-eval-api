"""Two-file submission matching: does this Lean file answer that problem?

The input shape is one problem file and one submission file, one theorem
per problem. The question is not "does the submission compile" -- a file
can compile perfectly and have quietly restated the theorem.

Fixtures follow the formal-conjectures conventions, because those are what
make the naive answer wrong: `sorry` is a legitimate body for an open
conjecture, and `answer(sorry)` is a hole the submission is *supposed* to
fill, so verbatim comparison would flag every solved problem.
"""

from __future__ import annotations

import pytest

from ftp_eval.formalizing.lean_file import (
    extract_answer_arguments,
    normalize_signature,
    parse_lean_file,
)
from ftp_eval.formalizing.matching import (
    MatchStatus,
    MismatchKind,
    SubmissionMatcher,
    match_submission,
)

PROBLEM = """\
module
public import FormalConjecturesUtil

namespace Erdos1

/-- A sum-distinct set. -/
abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective

/-- The minimal N for a 3-element sum-distinct set. -/
@[category research open, AMS 5 11]
theorem erdos_1.least_N_3 (h : 0 < 3) :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(sorry) := by
  sorry

end Erdos1
"""

#: The problem with the answer filled in but still no proof.
FILLED = PROBLEM.replace("answer(sorry)", "answer(4)")

#: A complete, honest submission.
SOLVED = FILLED.replace(
    ":= by\n  sorry", ":= by\n  refine ⟨⟨{1, 2, 4}, by decide, by decide⟩, ?_⟩\n  decide"
)

TARGET = "erdos_1.least_N_3"


def match(submission: str, **kw):
    return match_submission(PROBLEM, submission, **kw)


def kinds(report) -> set[str]:
    return {m.kind.value for m in report.mismatches}


# -- the accepting case ------------------------------------------------


def test_an_honest_submission_matches():
    report = match(SOLVED)
    assert report.status is MatchStatus.MATCHED
    assert report.status.answers_the_problem
    assert report.answers == ("4",)
    assert not report.fatal_mismatches


def test_the_target_is_inferred_without_being_named():
    # One problem per file, so naming the theorem is not required.
    assert match(SOLVED).target == TARGET


def test_naming_the_target_explicitly_works_too():
    assert match(SOLVED, target=TARGET).status is MatchStatus.MATCHED


def test_reformatting_is_not_a_mismatch():
    # Line wrapping is not meaning.
    rewrapped = SOLVED.replace(
        "theorem erdos_1.least_N_3 (h : 0 < 3) :\n    IsLeast",
        "theorem erdos_1.least_N_3\n    (h : 0 < 3) : IsLeast",
    )
    assert match(rewrapped).status is MatchStatus.MATCHED


def test_added_comments_are_not_a_mismatch():
    commented = SOLVED.replace(
        "IsLeast {", "IsLeast /- the least such N -/ {"
    )
    assert match(commented).status is MatchStatus.MATCHED


# -- filling the answer hole -------------------------------------------


def test_filling_the_answer_hole_is_expected_not_tampering():
    """The central case the format forces.

    `answer(sorry)` -> `answer(4)` changes the statement text, and it is
    exactly what a solution is supposed to do. A verbatim comparison would
    reject every solved problem.
    """
    report = match(SOLVED)
    assert MismatchKind.STATEMENT_CHANGED.value not in kinds(report)
    assert report.answers == ("4",)


def test_an_unfilled_answer_hole_is_reported():
    report = match(PROBLEM.replace(":= by\n  sorry", ":= by\n  decide"))
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.ANSWER_NOT_FILLED.value in kinds(report)


def test_removing_the_answer_hole_is_reported():
    without = FILLED.replace("} answer(4)", "} 4").replace(":= by\n  sorry", ":= by\n  decide")
    assert MismatchKind.ANSWER_HOLES_CHANGED.value in kinds(match(without))


def test_answer_arguments_are_bracket_matched():
    # A nested call must come back whole, not cut at the first ")".
    assert extract_answer_arguments("x = answer(Finset.card {1, 2})") == (
        "Finset.card {1, 2}",
    )
    assert extract_answer_arguments("answer(sorry) ↔ P") == ("sorry",)
    assert extract_answer_arguments("no holes here") == ()


# -- an honest non-answer ----------------------------------------------


def test_right_statement_with_no_proof_is_not_a_mismatch():
    """The distinction that matters most for scoring.

    A submission that states the problem correctly and has not proved it is
    an honest miss. Reporting it as tampering, or as reward hacking for the
    `sorry`, would turn "did not solve it" into "cheated".
    """
    report = match(FILLED)
    assert report.status is MatchStatus.MATCHED_BUT_UNPROVED
    assert not report.fatal_mismatches
    assert MismatchKind.PROOF_MISSING.value in kinds(report)
    assert MismatchKind.REWARD_HACKING.value not in kinds(report)


def test_sorry_in_the_problems_own_statement_is_not_held_against_the_submission():
    # An open conjecture is *stated* with a sorry body; that is the format,
    # not a violation.
    problem_declaration = parse_lean_file(PROBLEM).by_name()[TARGET]
    assert problem_declaration.is_unproved


# -- statement subversion ----------------------------------------------


def test_weakening_the_claim_is_caught():
    weakened = (
        FILLED.replace("IsLeast { N |", "(4 : ℕ) ∈ { N |")
        .replace("} answer(4)", "}")
        .replace(":= by\n  sorry", ":= by\n  decide")
    )
    report = match(weakened)
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.STATEMENT_CHANGED.value in kinds(report)


def test_adding_a_hypothesis_is_caught():
    stronger_assumption = FILLED.replace(
        "(h : 0 < 3)", "(h : 0 < 3) (hcheat : False)"
    ).replace(":= by\n  sorry", ":= by\n  exact absurd rfl hcheat")
    report = match(stronger_assumption)
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.STATEMENT_CHANGED.value in kinds(report)


def test_changing_a_bound_is_caught():
    changed = FILLED.replace("A.card = 3", "A.card = 2").replace(
        ":= by\n  sorry", ":= by\n  decide"
    )
    assert match(changed).status is MatchStatus.MISMATCHED


def test_redefining_a_dependency_is_caught():
    """The text of the theorem is untouched; its meaning is not.

    Redefining `IsSumDistinctSet` to `True` makes the theorem trivial while
    leaving its statement byte-identical, so comparing only the statement
    would pass this.
    """
    gutted = FILLED.replace(
        "A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective", "True"
    ).replace(":= by\n  sorry", ":= by\n  decide")
    report = match(gutted)
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.DEPENDENCY_CHANGED.value in kinds(report)


def test_deleting_a_dependency_is_caught():
    without = "\n".join(
        line
        for line in FILLED.splitlines()
        if "abbrev IsSumDistinctSet" not in line and "A ⊆ Finset.Icc" not in line
    ).replace(":= by\n  sorry", ":= by\n  decide")
    assert MismatchKind.DEPENDENCY_MISSING.value in kinds(match(without))


def test_proving_a_differently_named_theorem_is_missing_not_matched():
    renamed = SOLVED.replace("theorem erdos_1.least_N_3", "theorem my_own_lemma")
    report = match(renamed)
    assert report.status is MatchStatus.MISSING
    assert "my_own_lemma" in report.mismatches[0].detail


def test_turning_the_theorem_into_a_definition_is_caught():
    as_def = SOLVED.replace("theorem erdos_1.least_N_3", "abbrev erdos_1.least_N_3")
    assert MismatchKind.KIND_CHANGED.value in kinds(match(as_def))


# -- reward hacking, at the right scope --------------------------------


def test_an_axiom_anywhere_in_the_file_is_caught():
    """Scope matters: the axiom is outside the target's own block.

    `axiom cheat` declared after `end Erdos1` can still be cited by the
    proof, so screening only the target's text misses it entirely.
    """
    with_axiom = SOLVED.replace("end Erdos1", "axiom cheat : True\n\nend Erdos1")
    report = match(with_axiom)
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.REWARD_HACKING.value in kinds(report)


def test_a_sorry_inside_the_targets_proof_is_caught():
    partial = FILLED.replace(
        ":= by\n  sorry", ":= by\n  refine ⟨?_, ?_⟩\n  · decide\n  · sorry"
    )
    report = match(partial)
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.REWARD_HACKING.value in kinds(report)


def test_native_decide_is_caught():
    cheated = FILLED.replace(":= by\n  sorry", ":= by\n  native_decide")
    assert MismatchKind.REWARD_HACKING.value in kinds(match(cheated))


def test_dropping_an_import_is_a_note_not_a_failure():
    report = match(SOLVED.replace("public import FormalConjecturesUtil", ""))
    assert report.status is MatchStatus.MATCHED
    assert MismatchKind.IMPORT_REMOVED.value in kinds(report)
    assert not report.fatal_mismatches


def test_helper_lemmas_are_noted_not_penalized():
    with_helper = SOLVED.replace(
        "/-- The minimal N",
        "lemma helper : (1 : ℕ) + 2 = 3 := by decide\n\n/-- The minimal N",
    )
    report = match(with_helper)
    assert report.status is MatchStatus.MATCHED
    assert MismatchKind.EXTRA_STATEMENTS.value in kinds(report)
    assert not report.fatal_mismatches


# -- convention checks -------------------------------------------------


def test_a_multi_theorem_problem_file_is_reported():
    """One problem per file is the convention; verify rather than assume.

    If it is violated and no target is named, some theorem was picked and
    every verdict is about that one, so the inference is made visible.
    """
    extra = "@[category textbook]\ntheorem other : (1 : ℕ) = 1 := by rfl\n\nend Erdos1"
    two_problem = PROBLEM.replace("end Erdos1", extra)
    two_solved = SOLVED.replace("end Erdos1", extra)
    report = match_submission(two_problem, two_solved)
    assert MismatchKind.AMBIGUOUS_TARGET.value in kinds(report)
    # A dataset-convention note, not a submission failure: the submission
    # itself is fine, so the verdict must not be downgraded by it.
    assert not report.fatal_mismatches
    assert report.status is MatchStatus.MATCHED


def test_naming_the_target_silences_the_ambiguity_note():
    two = PROBLEM.replace(
        "end Erdos1", "theorem other : (1 : ℕ) = 1 := by rfl\n\nend Erdos1"
    )
    report = match_submission(two, two, target=TARGET)
    assert MismatchKind.AMBIGUOUS_TARGET.value not in kinds(report)


def test_a_problem_file_with_no_statement_is_unparsed():
    report = match_submission("abbrev X : Prop := True", SOLVED)
    assert report.status is MatchStatus.UNPARSED


def test_report_round_trips_and_formats():
    import json

    report = match(SOLVED)
    assert json.loads(json.dumps(report.to_dict()))["status"] == "matched"
    assert TARGET in report.format_text()


# -- parsing -----------------------------------------------------------


def test_attributes_spanning_several_lines_are_attached():
    source = (
        '@[category research solved, AMS 5 11, formal_proof using lean4 at\n'
        '  "https://example.com/x.lean#L1"]\n'
        "theorem t : True := by trivial\n"
    )
    declaration = parse_lean_file(source).by_name()["t"]
    assert declaration.categories == ("research", "solved")
    assert declaration.ams_tags == ("5", "11")


def test_the_closing_end_is_not_part_of_the_last_proof():
    """Otherwise `is_unproved` is wrong for the target every time.

    With one theorem per file the target *is* the last declaration, so an
    absorbed `end Namespace` broke exactly the primary case.
    """
    parsed = parse_lean_file(PROBLEM)
    declaration = parsed.by_name()[TARGET]
    assert "end Erdos1" not in declaration.body
    assert declaration.is_unproved
    assert "end Erdos1" in parsed.epilogue


def test_a_proof_line_ending_in_a_bracket_does_not_steal_the_boundary():
    source = (
        "theorem a : True := by\n"
        "  simp [Nat.add_comm]\n"
        "\n"
        "theorem b : True := by trivial\n"
    )
    parsed = parse_lean_file(source)
    assert {d.name for d in parsed.declarations} == {"a", "b"}
    assert "theorem a" not in parsed.by_name()["b"].source


def test_definitions_and_statements_are_separated():
    parsed = parse_lean_file(PROBLEM)
    assert [d.name for d in parsed.definitions()] == ["IsSumDistinctSet"]
    assert [d.name for d in parsed.statements()] == [TARGET]


def test_normalize_keeps_everything_except_holes_and_whitespace():
    assert normalize_signature(": P  =\n  answer(4)") == normalize_signature(": P = answer(9)")
    assert normalize_signature(": P = answer(4)") != normalize_signature(": P ≤ answer(4)")


def test_empty_file_parses_to_nothing():
    parsed = parse_lean_file("")
    assert parsed.declarations == ()


# -- prover-backed confirmation ---------------------------------------


def test_confirmation_source_states_the_problem_and_uses_the_submitted_proof():
    """The check textual comparison cannot make.

    Stating the *problem's* theorem and closing it with the *submission's*
    proof term settles equivalence exactly, since the prover decides it.
    """
    probe = SubmissionMatcher().build_confirmation_source(PROBLEM, SOLVED)
    assert probe is not None
    assert "ftp_eval_match_probe" in probe
    # The problem's hole is filled from the submission, since an unfilled
    # hole cannot be typechecked.
    assert "answer(4)" in probe
    assert "answer(sorry)" not in probe
    # The dependency travels with it, or the statement would not elaborate.
    assert "IsSumDistinctSet" in probe
    assert "decide" in probe


def test_no_confirmation_source_when_there_is_no_proof_to_use():
    # None must read as "not confirmed", never as "confirmed".
    assert SubmissionMatcher().build_confirmation_source(PROBLEM, FILLED) is None


def test_no_confirmation_source_when_the_hole_counts_differ():
    mismatched = FILLED.replace("} answer(4)", "} 4").replace(
        ":= by\n  sorry", ":= by\n  decide"
    )
    assert SubmissionMatcher().build_confirmation_source(PROBLEM, mismatched) is None


@pytest.mark.parametrize("bad", ["", "-- just a comment", "import Mathlib"])
def test_no_confirmation_source_from_an_unusable_problem(bad):
    assert SubmissionMatcher().build_confirmation_source(bad, SOLVED) is None


# -- an answer that brings its own machinery ---------------------------
#
# A real proof often needs many auxiliary definitions and lemmas, so the
# answer file can be far larger than the theorem file. None of that is
# penalised. What changes is that there are many more places to hide an
# unproved assumption, so the scoping of the checks has to be right.

MANY_EXTRAS = FILLED.replace(
    "@[category research open, AMS 5 11]",
    """\
/-- A helper the answer introduces for itself. -/
abbrev SubsetSums (A : Finset ℕ) : Finset ℕ :=
    A.powerset.image (fun S => S.sum id)

/-- Another one. -/
def IsGood (N : ℕ) : Prop := 0 < N

lemma sums_card (A : Finset ℕ) : (SubsetSums A).card ≤ 2 ^ A.card := by
  decide

lemma good_four : IsGood 4 := by decide

@[category research solved, AMS 5 11]""",
).replace(
    ":= by\n  sorry",
    ":= by\n  have h := sums_card {1, 2, 4}\n  have g := good_four\n  refine ⟨⟨{1, 2, 4}, by decide, by decide⟩, ?_⟩\n  decide",
)


def test_an_answer_may_add_as_many_definitions_as_it_likes():
    report = match(MANY_EXTRAS)
    assert report.status is MatchStatus.MATCHED
    assert not report.fatal_mismatches
    counts = report.definition_counts
    assert counts["added"] == 2          # SubsetSums, IsGood
    assert counts["unchanged"] == 1      # IsSumDistinctSet, kept as-is
    assert "added=2" in report.format_text()


def test_added_definitions_are_marked_not_load_bearing():
    by_name = {d.name: d for d in match(MANY_EXTRAS).definitions}
    assert by_name["IsSumDistinctSet"].load_bearing      # the statement names it
    assert not by_name["SubsetSums"].load_bearing        # the answer's own
    assert by_name["SubsetSums"].status.value == "added"


def test_extra_helper_lemmas_are_noted_but_not_penalized():
    report = match(MANY_EXTRAS)
    assert MismatchKind.EXTRA_STATEMENTS.value in kinds(report)
    assert all(
        not m.fatal
        for m in report.mismatches
        if m.kind is MismatchKind.EXTRA_STATEMENTS
    )


def test_a_helper_lemma_left_unproved_and_cited_is_fatal():
    """The risk that grows with the number of extras.

    `lemma key : ... := by sorry` plus `exact key` in the target is a proof
    conditional on an assumption. Scoping the placeholder check to the
    target's own block -- which is what the single-file case wants -- would
    miss this entirely.
    """
    # Supply the target's proof first, so inserting the helper afterwards
    # leaves exactly one `sorry` in the file -- the helper's.
    smuggled = FILLED.replace(":= by\n  sorry", ":= by\n  exact key").replace(
        "@[category research open, AMS 5 11]",
        "lemma key : IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } 4 := by\n"
        "  sorry\n\n@[category research solved, AMS 5 11]",
    )
    report = match(smuggled)
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.UNPROVED_HELPER.value in kinds(report)
    assert "conditional on" in report.format_text()


def test_an_unproved_helper_the_target_cites_is_fatal_even_among_many():
    # MANY_EXTRAS' target cites good_four, so gutting it makes the proof
    # conditional on an assumption.
    cited = MANY_EXTRAS.replace(
        "lemma good_four : IsGood 4 := by decide",
        "lemma good_four : IsGood 4 := by sorry",
    )
    report = match(cited)
    assert MismatchKind.UNPROVED_HELPER.value in kinds(report)
    assert report.status is MatchStatus.MISMATCHED


def test_an_uncited_unproved_helper_is_reported_without_failing():
    # Dead weight: worth surfacing, but it licenses nothing.
    dead = MANY_EXTRAS.replace(
        "lemma sums_card (A : Finset ℕ) : (SubsetSums A).card ≤ 2 ^ A.card := by\n  decide",
        "lemma unused_side_claim : (1 : ℕ) = 1 := by\n  sorry",
    ).replace("  have h := sums_card {1, 2, 4}\n", "")
    report = match(dead)
    assert MismatchKind.UNPROVED_HELPER.value in kinds(report)
    assert report.status is MatchStatus.MATCHED
    assert not report.fatal_mismatches


def test_an_added_definition_that_shadows_the_theorems_vocabulary_is_caught():
    # "Adding" a definition that happens to reuse a problem name is not
    # adding, it is redefining.
    shadowing = MANY_EXTRAS.replace(
        "abbrev SubsetSums (A : Finset ℕ) : Finset ℕ :=\n    A.powerset.image (fun S => S.sum id)",
        "abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop := True",
    )
    report = match(shadowing)
    assert report.status is MatchStatus.MISMATCHED
    assert MismatchKind.DEPENDENCY_CHANGED.value in kinds(report)


def test_the_probe_uses_the_theorems_definitions_not_the_answers():
    """The soundness property of the confirmation step.

    If the probe were built from the answer's definitions, an answer that
    replaces `IsSumDistinctSet` with `True` would be compiled against its
    own gutted vocabulary and confirm against a claim nobody asked for.
    """
    gutted = FILLED.replace(
        "A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective", "True"
    ).replace(":= by\n  sorry", ":= by\n  decide")
    probe = SubmissionMatcher().build_confirmation_source(PROBLEM, gutted)
    assert probe is not None
    # The theorem file's real definition is what the probe elaborates against.
    assert "Finset.Icc 1 N" in probe
    assert "abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop := True" not in probe


def test_the_probe_carries_the_answers_own_helpers():
    # The answer's proof cites them, so they have to come along or the probe
    # would fail for the wrong reason.
    probe = SubmissionMatcher().build_confirmation_source(PROBLEM, MANY_EXTRAS)
    assert probe is not None
    assert "SubsetSums" in probe
    assert "sums_card" in probe
    assert "ftp_eval_match_probe" in probe


def test_the_probe_drops_unproved_helpers():
    # An unproved helper must not be smuggled into the probe, where it would
    # silently license the proof.
    smuggled = FILLED.replace(
        "@[category research open, AMS 5 11]",
        "lemma key : True := by sorry\n\n@[category research solved, AMS 5 11]",
    ).replace(":= by\n  sorry", ":= by\n  exact ⟨⟨{1, 2, 4}, by decide, by decide⟩, by decide⟩")
    probe = SubmissionMatcher().build_confirmation_source(PROBLEM, smuggled)
    assert probe is not None
    assert "lemma key" not in probe
