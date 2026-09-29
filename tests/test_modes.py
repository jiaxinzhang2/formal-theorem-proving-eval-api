"""Failure-mode and success-mode classification.

Both directions, because a pass rate throws away information in both: why
a failure failed decides what to fix, and what *kind* of proof passed
decides whether "solved 40%" means what it sounds like.
"""

from __future__ import annotations

import pytest

from ftp_eval import (
    Attribution,
    Diagnostic,
    ErrorKind,
    FailureMode,
    ProofAttempt,
    ProofTask,
    Severity,
    Status,
    SuccessMode,
    analyze_proof,
    classify_failure,
    classify_success,
    create,
    extract_tactics,
    looks_truncated,
    summarize,
)
from ftp_eval.analysis.modes import (
    aggregate_failure_modes,
    aggregate_success_modes,
    hallucinated_names,
)


def err(message: str, line: int | None = None) -> Diagnostic:
    return Diagnostic(Severity.ERROR, message, line=line)


def classify(message: str, *, kind=ErrorKind.UNKNOWN, proof=" nlinarith", **kw):
    return classify_failure(Status.FAILED, kind, (err(message),), proof=proof, **kw)


# -- the distinctions ErrorKind cannot make ---------------------------


def test_hallucinated_lemma_is_distinct_from_a_type_error():
    assert classify("unknown identifier 'Nat.add_le_of_lt_succ'") is FailureMode.UNKNOWN_LEMMA
    assert classify("type mismatch") is FailureMode.TYPE_MISMATCH


def test_hallucinated_tactic_is_distinct_from_a_hallucinated_lemma():
    assert classify("unknown tactic 'nlinarith_plus'") is FailureMode.UNKNOWN_TACTIC


def test_missing_instance_is_distinct_from_a_type_mismatch():
    assert classify("failed to synthesize instance Ring α") is FailureMode.MISSING_INSTANCE


@pytest.mark.parametrize(
    "message,expected",
    [
        ("linarith failed to find a contradiction", FailureMode.LINARITH_FAILED),
        ("nlinarith failed", FailureMode.NLINARITH_FAILED),
        ("omega could not prove the goal", FailureMode.OMEGA_FAILED),
        ("simp made no progress", FailureMode.SIMP_NO_PROGRESS),
        ("did not find instance of the pattern in the target", FailureMode.RW_PATTERN_NOT_FOUND),
        ("positivity failed", FailureMode.POSITIVITY_FAILED),
        ("aesop failed to prove the goal", FailureMode.AESOP_FAILED),
        ("unsolved goals", FailureMode.UNSOLVED_GOALS),
        ("application type mismatch", FailureMode.APPLICATION_MISMATCH),
        ("function expected at h", FailureMode.FUNCTION_EXPECTED),
        ("maximum recursion depth has been reached", FailureMode.RECURSION_DEPTH),
        ("(deterministic) timeout at whnf", FailureMode.HEARTBEAT_EXCEEDED),
    ],
)
def test_each_automation_failure_gets_its_own_mode(message, expected):
    # `tactic_failed` for all of these would hide which automation is
    # running out of road, which is the useful thing to know.
    assert classify(message) is expected


# -- truncation, the expensive confusion ------------------------------


def test_unexpected_end_of_input_is_truncation_not_bad_syntax():
    assert classify("unexpected end of input") is FailureMode.TRUNCATED_OUTPUT


def test_unbalanced_brackets_with_a_syntax_error_reads_as_truncation():
    mode = classify("unexpected token", proof=" nlinarith [sq_nonneg n, sq_nonneg (a - b")
    assert mode is FailureMode.TRUNCATED_OUTPUT


def test_a_genuine_syntax_error_is_not_called_truncation():
    mode = classify("unexpected token ':='; expected term", proof=" intro h := foo")
    assert mode is FailureMode.UNEXPECTED_TOKEN


def test_truncation_is_attributed_to_budget_not_the_model():
    assert FailureMode.TRUNCATED_OUTPUT.attribution is Attribution.BUDGET
    assert FailureMode.UNKNOWN_LEMMA.attribution is Attribution.MODEL


@pytest.mark.parametrize(
    "proof",
    [
        " nlinarith [sq_nonneg (a - b",     # unbalanced bracket
        " have h : a = b :=",               # ends on an assignment
        " rw",                              # ends on a tactic keyword
        " simp [foo,",                      # ends on a comma
    ],
)
def test_looks_truncated_catches_cut_off_proofs(proof):
    assert looks_truncated(proof)


@pytest.mark.parametrize(
    "proof",
    [" nlinarith [sq_nonneg (a - b)]", " simp", " exact h", ""],
)
def test_looks_truncated_does_not_fire_on_complete_proofs(proof):
    assert not looks_truncated(proof)


# -- attribution ------------------------------------------------------


def test_reward_hacking_is_classified_before_anything_else():
    mode = classify_failure(
        Status.REJECTED, ErrorKind.SOUNDNESS, (), proof=" sorry", soundness_ok=False
    )
    assert mode is FailureMode.REWARD_HACKING
    assert mode.attribution is Attribution.SOUNDNESS


def test_toolchain_and_harness_errors_are_not_the_models_fault():
    assert (
        classify_failure(Status.ERROR, ErrorKind.TOOLCHAIN, (), proof="x").attribution
        is Attribution.HARNESS
    )
    assert (
        classify_failure(Status.ERROR, ErrorKind.HARNESS, (), proof="x").attribution
        is Attribution.HARNESS
    )


def test_wall_clock_timeout_is_a_budget_problem():
    mode = classify_failure(Status.TIMEOUT, ErrorKind.TIMEOUT, (), proof=" nlinarith")
    assert mode is FailureMode.WALL_CLOCK_TIMEOUT
    assert mode.attribution is Attribution.BUDGET


def test_empty_proof_has_its_own_mode():
    assert classify_failure(Status.FAILED, ErrorKind.INCOMPLETE, (), proof="   ") is FailureMode.EMPTY_PROOF


def test_a_verified_attempt_has_no_failure_mode():
    assert classify_failure(Status.VERIFIED, None, ()) is None


def test_unrecognized_message_falls_back_to_the_coarse_kind():
    # Never a confident specific guess when the message is unfamiliar.
    assert classify("something nobody has seen", kind=ErrorKind.UNSOLVED_GOALS) is FailureMode.UNSOLVED_GOALS
    assert classify("something nobody has seen", kind=ErrorKind.UNKNOWN) is FailureMode.UNCLASSIFIED


# -- what the model invents -------------------------------------------


def test_extracts_the_names_the_prover_says_do_not_exist():
    diagnostics = (
        err("unknown identifier 'Nat.foo_bar'"),
        err("unknown constant 'Real.baz'"),
        err("type mismatch"),
    )
    assert hallucinated_names(diagnostics) == ["Nat.foo_bar", "Real.baz"]


def test_invented_names_are_ranked_in_the_report():
    entries = [
        (FailureMode.UNKNOWN_LEMMA, (err("unknown identifier 'Nat.fake'"),), "m", 3, 10)
        for _ in range(4)
    ]
    stats = aggregate_failure_modes(entries)
    assert stats.invented_names == {"Nat.fake": 4}
    assert "Nat.fake(4)" in stats.format_text()


# -- failure-mode aggregation -----------------------------------------


def test_budget_fraction_warns_when_the_harness_is_the_problem():
    entries = [(FailureMode.TRUNCATED_OUTPUT, (), "m", 0, 0) for _ in range(3)]
    entries += [(FailureMode.UNKNOWN_LEMMA, (), "m", 0, 0) for _ in range(7)]
    stats = aggregate_failure_modes(entries)
    assert stats.budget_fraction == pytest.approx(0.3)
    assert "budget, not capability" in stats.format_text()


def test_no_budget_warning_when_failures_are_genuine():
    entries = [(FailureMode.UNKNOWN_LEMMA, (), "m", 0, 0) for _ in range(10)]
    assert "budget, not capability" not in aggregate_failure_modes(entries).format_text()


def test_first_error_position_is_normalized():
    entries = [(FailureMode.UNSOLVED_GOALS, (), None, 8, 10)]
    stats = aggregate_failure_modes(entries)
    assert stats.mean_first_error_position == pytest.approx(0.8)


def test_failure_modes_split_by_model():
    entries = [
        (FailureMode.TRUNCATED_OUTPUT, (), "weak", 0, 0),
        (FailureMode.UNKNOWN_LEMMA, (), "strong", 0, 0),
    ]
    stats = aggregate_failure_modes(entries)
    assert stats.by_model["weak"] == {"truncated_output": 1}
    assert stats.by_model["strong"] == {"unknown_lemma": 1}


# -- success modes ----------------------------------------------------


def success_of(proof: str) -> SuccessMode:
    tactics = extract_tactics(proof)
    return classify_success(tactics, analyze_proof(proof).to_dict())


def test_one_line_automation_is_recognized():
    assert success_of("by simp") is SuccessMode.ONE_LINER_AUTOMATION
    assert success_of("by omega") is SuccessMode.ONE_LINER_AUTOMATION


def test_decide_is_separated_from_other_one_liners():
    # Closed by computation rather than argument, which is worth its own
    # bucket: a rising share means the benchmark is drifting.
    assert success_of("by decide") is SuccessMode.BRUTE_FORCE_DECIDE
    assert success_of("by native_decide") is SuccessMode.BRUTE_FORCE_DECIDE


def test_term_mode_proof_is_recognized():
    assert success_of("Nat.le_refl n") is SuccessMode.TERM_MODE


def test_structured_proof_with_named_steps():
    proof = "by\n  have k : 0 < n := h\n  nlinarith [k]"
    assert success_of(proof) is SuccessMode.STRUCTURED_WITH_STEPS


def test_case_analysis_is_recognized():
    assert success_of("by\n  rcases h with h1 | h2\n  simp\n  omega") is SuccessMode.CASE_ANALYSIS


def test_induction_outranks_case_analysis():
    proof = "by\n  induction n with\n  | zero => simp\n  | succ k ih => rcases ih with a\n  omega"
    assert success_of(proof) is SuccessMode.INDUCTION


def test_auxiliary_lemmas_outrank_everything():
    proof = "lemma helper : True := trivial\ntheorem main : True := by\n  induction 0 <;> exact helper"
    assert success_of(proof) is SuccessMode.AUXILIARY_LEMMAS


def test_calc_chain_is_recognized():
    proof = "by\n  calc a = b := by rw [hab]\n    _ <= c := hbc"
    assert success_of(proof) is SuccessMode.CALC_CHAIN


def test_long_tactic_chain_is_separated_from_short():
    short = "by\n  intro h\n  simp\n  omega"
    long = "by\n" + "".join("  simp [f%d] at h\n" % i for i in range(8)) + "  omega"
    assert success_of(short) is SuccessMode.SHORT_TACTIC_CHAIN
    assert success_of(long) is SuccessMode.LONG_TACTIC_CHAIN


def test_substantive_modes_are_marked():
    assert SuccessMode.INDUCTION.is_substantive
    assert SuccessMode.STRUCTURED_WITH_STEPS.is_substantive
    assert not SuccessMode.ONE_LINER_AUTOMATION.is_substantive
    assert not SuccessMode.BRUTE_FORCE_DECIDE.is_substantive


def test_success_mode_aggregation_reports_the_automation_share():
    entries = [(SuccessMode.ONE_LINER_AUTOMATION, "m")] * 9
    entries += [(SuccessMode.INDUCTION, "m")]
    stats = aggregate_success_modes(entries)
    assert stats.automation_fraction == pytest.approx(0.9)
    assert stats.substantive_fraction == pytest.approx(0.1)
    text = stats.format_text()
    assert "90% closed by automation" in text
    assert "measuring tactic coverage" in text


def test_no_automation_note_for_a_varied_run():
    entries = [(SuccessMode.INDUCTION, "m"), (SuccessMode.ONE_LINER_AUTOMATION, "m")]
    assert "measuring tactic coverage" not in aggregate_success_modes(entries).format_text()


# -- wired end to end -------------------------------------------------


def test_verify_labels_a_pass_with_a_success_mode_and_no_failure_mode():
    backend = create("mock")
    task = ProofTask(task_id="t", formal_statement="theorem t (n : Nat) : n = n := by")
    result = backend.verify(
        task, ProofAttempt(task_id="t", proof=" have k : True := trivial\n  simp MOCK_PASS")
    )
    assert result.status is Status.VERIFIED
    assert result.success_mode == SuccessMode.STRUCTURED_WITH_STEPS.value
    assert result.failure_mode is None


def test_verify_labels_a_failure_with_a_failure_mode_and_no_success_mode():
    backend = create("mock")
    task = ProofTask(task_id="t", formal_statement="theorem t : True := by")
    result = backend.verify(task, ProofAttempt(task_id="t", proof=" simp MOCK_UNSOLVED"))
    assert result.status is Status.FAILED
    assert result.failure_mode is not None
    assert result.success_mode is None


def test_reward_hacking_gets_the_soundness_failure_mode():
    backend = create("mock")
    task = ProofTask(task_id="t", formal_statement="theorem t : True := by")
    result = backend.verify(task, ProofAttempt(task_id="t", proof=" MOCK_PASS ; sorry"))
    assert result.status is Status.REJECTED
    assert result.failure_mode == FailureMode.REWARD_HACKING.value


def test_summary_reports_both_mode_tables():
    backend = create("mock")
    task = ProofTask(task_id="t", formal_statement="theorem t : True := by")
    results = [
        backend.verify(task, ProofAttempt(task_id="t", proof=" simp MOCK_PASS", sample_index=0)),
        backend.verify(task, ProofAttempt(task_id="t", proof=" simp MOCK_FAIL", sample_index=1)),
    ]
    summary = summarize(results, ks=(1,))
    assert summary.failure_modes.failures == 1
    assert summary.success_modes.successes == 1
    text = summary.format_text(include_tactics=True)
    assert "failure modes" in text
    assert "success modes" in text
    assert "failure_modes" in summary.to_dict()


def test_modes_round_trip_through_json():
    import json

    from ftp_eval import VerificationResult

    original = VerificationResult(
        task_id="a",
        attempt_id="a#0",
        backend="mock",
        status=Status.FAILED,
        failure_mode=FailureMode.UNKNOWN_LEMMA.value,
    )
    restored = VerificationResult.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.failure_mode == "unknown_lemma"
    assert restored.success_mode is None
