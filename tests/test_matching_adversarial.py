"""Adversarial tests: trying to make the matcher say the wrong thing.

This is a contest grader, so participants have an incentive to game it.
Every case here is an attack that *worked* when it was first tried, and
each one names the direction it fails in, because the two are not equally
bad:

  MISS  -- an answer that does not prove the theorem is accepted. A wrong
           name on a leaderboard, and invisible.
  NOISE -- an honest answer is flagged. Reviewable, so tolerable.

The bar is zero misses. Noise is traded for that deliberately.
"""

from __future__ import annotations

import pytest

from ftp_eval.proving.matching import MatchStatus, MismatchKind, match_submission

PROBLEM = """\
module
public import FormalConjecturesUtil

namespace Demo

abbrev IsGood (A : Finset ℕ) (N : ℕ) : Prop :=
    A ⊆ Finset.Icc 1 N ∧ A.card = 3

@[category research open, AMS 5 11]
theorem demo_thm (h : 0 < 3) :
    IsLeast { N | ∃ A, IsGood A N } answer(sorry) := by
  sorry

end Demo
"""

HONEST = PROBLEM.replace("answer(sorry)", "answer(4)").replace(
    ":= by\n  sorry", ":= by\n  exact ⟨⟨{1,2,4}, by decide, by decide⟩, by decide⟩"
)


def accepted(answer: str, problem: str = PROBLEM) -> bool:
    return match_submission(problem, answer).status.answers_the_problem


def kinds(answer: str, problem: str = PROBLEM) -> set[str]:
    return {m.kind.value for m in match_submission(problem, answer).mismatches}


def test_the_honest_answer_is_accepted():
    # The control. Without it, "rejects everything" would pass this file.
    assert accepted(HONEST)


# -- normalization collisions -----------------------------------------


def test_a_string_literal_containing_a_comment_marker_cannot_collide():
    """MISS when regex comment-stripping ate the rest of the line.

    Two statements differing only inside a string both normalized to the
    truncated prefix, so a changed claim compared equal.
    """
    problem = PROBLEM.replace(
        "IsLeast { N | ∃ A, IsGood A N } answer(sorry)",
        'note = "keep -- ALPHA" ∧ IsLeast { N | ∃ A, IsGood A N } answer(sorry)',
    )
    answer = (
        problem.replace("ALPHA", "OMEGA")
        .replace("answer(sorry)", "answer(4)")
        .replace(":= by\n  sorry", ":= by\n  decide")
    )
    assert not accepted(answer, problem)


def test_a_nested_block_comment_is_not_read_as_live_code():
    # NOISE: Lean nests block comments, so this is all comment. Flagging it
    # would reject an honest file.
    assert accepted(HONEST.replace("end Demo", "/- outer /- inner -/ axiom x : False -/\n\nend Demo"))


def test_a_non_breaking_space_does_not_break_an_honest_match():
    # NOISE direction.
    assert accepted(HONEST.replace("IsLeast { N |", "IsLeast { N |"))


def test_a_homoglyph_in_the_theorem_name_is_not_the_theorem():
    assert not accepted(HONEST.replace("theorem demo_thm", "theorem demo_tĥm"))


# -- identity of the target -------------------------------------------


def test_the_same_bare_name_under_another_namespace_is_a_different_theorem():
    """MISS when names were compared bare.

    `Other.demo_thm` is not `Demo.demo_thm`. Accepting it would let an
    answer prove whatever it liked as long as the last component agreed.
    """
    renamespaced = HONEST.replace("namespace Demo", "namespace Other").replace(
        "end Demo", "end Other"
    )
    assert not accepted(renamespaced)
    assert MismatchKind.NAMESPACE_CHANGED.value in kinds(renamespaced)


def test_the_theorem_declared_twice_is_refused_not_silently_resolved():
    """MISS when the lookup quietly took one of them.

    Lean rejects a duplicate declaration, so the file cannot compile --
    but a grader that picks one is choosing between a decoy and the real
    thing by accident.
    """
    twice = HONEST.replace(
        "@[category research open, AMS 5 11]",
        "theorem demo_thm (h : 0 < 3) : True := by trivial\n\n"
        "@[category research open, AMS 5 11]",
    )
    assert not accepted(twice)


def test_a_named_decoy_beside_an_anonymous_real_proof_is_refused():
    decoy = HONEST.replace(
        "theorem demo_thm (h : 0 < 3) :\n    IsLeast",
        "theorem demo_thm (h : 0 < 3) : True := by trivial\n\nexample (h : 0 < 3) :\n    IsLeast",
    )
    assert not accepted(decoy)


# -- hiding the change -------------------------------------------------


def test_the_original_statement_parked_in_a_comment_does_not_help():
    hidden = HONEST.replace(
        "IsLeast { N | ∃ A, IsGood A N } answer(4)",
        "/- IsLeast { N | ∃ A, IsGood A N } answer(4) -/ True",
    )
    assert not accepted(hidden)


def test_a_dependency_reached_indirectly_is_still_load_bearing():
    """MISS when dependencies were resolved only one level deep.

    The statement names `IsGood`; `IsGood`'s body names `Size`. Changing
    `Size` changes the claim just as surely, and a direct-only check called
    it an unrelated helper.
    """
    problem = PROBLEM.replace(
        "abbrev IsGood (A : Finset ℕ) (N : ℕ) : Prop :=\n    A ⊆ Finset.Icc 1 N ∧ A.card = 3",
        "abbrev Size : ℕ := 3\n\nabbrev IsGood (A : Finset ℕ) (N : ℕ) : Prop :=\n"
        "    A ⊆ Finset.Icc 1 N ∧ A.card = Size",
    )
    answer = (
        problem.replace("abbrev Size : ℕ := 3", "abbrev Size : ℕ := 0")
        .replace("answer(sorry)", "answer(4)")
        .replace(":= by\n  sorry", ":= by\n  decide")
    )
    assert not accepted(answer, problem)
    assert MismatchKind.DEPENDENCY_CHANGED.value in kinds(answer, problem)


# -- answer(...) abuse -------------------------------------------------


def test_moving_the_hole_keeps_the_count_but_changes_the_claim():
    moved = HONEST.replace(
        "IsLeast { N | ∃ A, IsGood A N } answer(4)",
        "IsLeast { N | ∃ A, IsGood A answer(4) } 4",
    )
    assert not accepted(moved)


def test_answer_used_as_an_ordinary_identifier_is_not_a_hole():
    # NOISE direction: a local named `answer` in the proof must not be
    # mistaken for a hole in the statement.
    assert accepted(HONEST.replace(":= by\n  exact", ":= by\n  let answer := 4\n  exact"))


# -- structural --------------------------------------------------------


@pytest.mark.parametrize(
    "answer,label",
    [
        ("", "empty file"),
        (PROBLEM, "the problem handed back verbatim"),
    ],
)
def test_degenerate_answers_are_refused(answer, label):
    assert not accepted(answer), label


def test_a_weakened_statement_with_the_real_one_only_in_show_is_refused():
    sleight = HONEST.replace(
        "IsLeast { N | ∃ A, IsGood A N } answer(4)", "True"
    ).replace(
        ":= by\n  exact", ":= by\n  show IsLeast { N | ∃ A, IsGood A N } 4\n  exact"
    )
    assert not accepted(sleight)
