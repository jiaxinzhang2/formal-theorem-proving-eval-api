"""Soundness screening is the load-bearing part of this package.

If these tests pass, a model cannot score by cheating in any of the ways
we know about. Each test names the specific trick it blocks.
"""

from __future__ import annotations

import pytest

from ftp_eval import (
    Assembly,
    ErrorKind,
    ProofAttempt,
    ProofTask,
    Status,
    create,
    screen_soundness,
)
from ftp_eval.proving.verifier import assemble_source, strip_comments


def task(statement: str = "theorem t (n : Nat) : n + 0 = n := by", **kw) -> ProofTask:
    kw.setdefault("header", "import Mathlib")
    return ProofTask(task_id="t", formal_statement=statement, **kw)


def screen(proof: str, t: ProofTask | None = None):
    t = t or task()
    attempt = ProofAttempt(task_id=t.task_id, proof=proof)
    return screen_soundness(t, attempt, assemble_source(t, attempt))


def test_clean_proof_passes_the_screen():
    assert screen(" simp").ok


@pytest.mark.parametrize("proof", [" sorry", " by sorry", " exact sorry", " simp\n  sorry"])
def test_sorry_is_caught(proof):
    report = screen(proof)
    assert not report.ok
    assert any("sorry" in v for v in report.violations)


def test_sorry_inside_a_comment_is_not_a_violation():
    # Otherwise "-- TODO: replace the sorry" fails an honest proof.
    assert screen(" simp -- no sorry here").ok
    assert screen(" simp\n/- sorry -/").ok


def test_sorry_hidden_behind_a_comment_is_still_caught():
    # The inverse trick: make the checker look at a comment instead.
    assert not screen(" /- clean -/ sorry").ok


def test_new_axiom_is_caught():
    t = task(assembly=Assembly.FULL_FILE)
    report = screen(
        "import Mathlib\naxiom cheat (n : Nat) : n + 0 = n\ntheorem t (n : Nat) : n + 0 = n := cheat n",
        t,
    )
    assert not report.ok
    assert any("axiom" in v for v in report.violations)


def test_disabling_the_heartbeat_limit_is_caught():
    t = task(assembly=Assembly.FULL_FILE)
    statement = "theorem t (n : Nat) : n + 0 = n := by simp"
    report = screen("import Mathlib\nset_option maxHeartbeats 0\n" + statement, t)
    assert not report.ok


def test_native_decide_is_caught():
    # native_decide trusts the compiler rather than the Lean kernel, so a
    # proof that leans on it is not a kernel-checked proof.
    assert not screen(" native_decide").ok


def test_statement_tampering_is_caught_in_full_file_mode():
    t = ProofTask(
        task_id="t",
        header="import Mathlib",
        formal_statement="theorem t (n : Nat) : n + 0 = n",
        assembly=Assembly.FULL_FILE,
    )
    # Proves something easier than what was asked.
    report = screen("import Mathlib\ntheorem t : 1 = 1 := rfl", t)
    assert not report.ok
    assert any("statement" in v for v in report.violations)


def test_matching_statement_passes_in_full_file_mode():
    t = ProofTask(
        task_id="t",
        header="import Mathlib",
        formal_statement="theorem t (n : Nat) : n + 0 = n",
        assembly=Assembly.FULL_FILE,
    )
    report = screen("import Mathlib\n\ntheorem t (n : Nat) : n + 0 = n := by\n  simp", t)
    assert report.ok, report.violations


def test_statement_is_not_checked_when_we_supplied_it():
    # Under CONTINUE_STATEMENT the statement cannot have been altered,
    # because the harness concatenated it itself.
    assert screen(" simp").ok


def test_verifier_downgrades_a_passing_but_unsound_proof():
    # The end-to-end guarantee: even when the backend says VERIFIED, an
    # unsound proof comes back REJECTED, never VERIFIED.
    backend = create("mock")
    t = task()
    result = backend.verify(t, ProofAttempt(task_id="t", proof=" MOCK_PASS; sorry"))
    assert result.status is Status.REJECTED
    assert result.error_kind is ErrorKind.SOUNDNESS
    assert not result.verified


def test_soundness_checking_can_be_disabled_explicitly():
    # Opt-out exists for debugging, and requires saying so out loud.
    backend = create("mock", check_soundness=False)
    result = backend.verify(task(), ProofAttempt(task_id="t", proof=" MOCK_PASS; sorry"))
    assert result.status is Status.VERIFIED


def test_strip_comments_handles_nested_block_syntax():
    assert "secret" not in strip_comments("a /- secret -/ b", "lean4")
    assert "keep" in strip_comments("keep /- drop -/", "lean4")
