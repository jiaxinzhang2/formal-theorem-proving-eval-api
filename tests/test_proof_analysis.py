"""Tactic extraction, structural metrics, repetition, and aggregation."""

from __future__ import annotations

import pytest

from ftp_eval import Status, VerificationResult, extract_tactics, summarize
from ftp_eval.proof_metrics import (
    aggregate_structure,
    analyze_proof,
    distribution,
    sample_duplication,
)
from ftp_eval.tactics import tactic_stats

PROOF = """\
by
  intro n h
  have key : 0 < n := h
  have step : n * 1 <= n * n := Nat.mul_le_mul_left n h
  -- finish with arithmetic
  nlinarith [key, step, sq_nonneg n]
"""


# -- tactic extraction ------------------------------------------------


def test_extracts_tactics_in_order_with_repeats():
    tactics = extract_tactics("by\n  intro h\n  simp\n  simp\n  omega")
    assert tactics == ("intro", "simp", "simp", "omega")


def test_ignores_tactics_named_in_comments():
    assert "sorry" not in extract_tactics("by\n  -- not a sorry\n  simp")


def test_finds_tactics_after_combinators():
    tactics = extract_tactics("by constructor <;> simp <;> omega")
    assert {"constructor", "simp", "omega"} <= set(tactics)


def test_does_not_count_hypothesis_names_as_tactics():
    tactics = extract_tactics("by\n  exact foo_bar_baz h")
    assert tactics == ("exact",)


def test_unknown_language_measures_nothing_rather_than_guessing():
    assert extract_tactics("by simp", language="agda") == ()


def test_empty_proof_yields_no_tactics():
    assert extract_tactics("") == ()


# -- tactic stats -----------------------------------------------------


def test_tactic_stats_split_verified_from_failed():
    stats = tactic_stats(
        [
            (("simp", "omega"), True, True),
            (("simp",), False, True),
            (("nlinarith",), False, True),
            (("simp",), False, False),  # a harness error: excluded from the split
        ]
    )
    assert stats.proofs_measured == 4
    assert stats.total["simp"] == 3
    assert stats.proofs_using["simp"] == 3
    assert stats.in_verified["simp"] == 1
    assert stats.in_failed["simp"] == 1  # not 2: the errored one is excluded
    assert stats.success_rate("omega") == pytest.approx(1.0)
    assert stats.success_rate("nlinarith") == pytest.approx(0.0)
    assert stats.success_rate("never_used") is None


def test_tactic_stats_counts_proofs_with_no_tactic():
    stats = tactic_stats([((), False, True), (("simp",), True, True)])
    assert stats.proofs_without_tactics == 1
    assert stats.mean_distinct_per_proof == pytest.approx(0.5)


def test_suspect_tactics_are_surfaced():
    stats = tactic_stats([(("sorry",), True, True)])
    assert stats.suspect_usage == {"sorry": 1}
    assert "soundness-relevant" in stats.format_text()


# -- structure --------------------------------------------------------


def test_counts_declarations_and_auxiliary_lemmas():
    source = "lemma helper : True := trivial\ntheorem main : True := helper"
    structure = analyze_proof(source)
    assert structure.declarations == {"lemma": 1, "theorem": 1}
    assert structure.auxiliary_declarations == 1


def test_counts_named_steps_and_local_dependency_depth():
    structure = analyze_proof(PROOF)
    assert structure.named_steps == 2
    # key -> step is not a chain (step does not cite key), but the final
    # nlinarith cites both, so the chain is 2 deep.
    assert structure.local_dependency_depth >= 2


def test_dependency_depth_follows_a_real_chain():
    proof = (
        "by\n"
        "  have a : True := trivial\n"
        "  have b : True := by exact a\n"
        "  have c : True := by exact b\n"
        "  exact c"
    )
    assert analyze_proof(proof).local_dependency_depth == 4


def test_independent_steps_do_not_stack_depth():
    proof = "by\n  have a : True := trivial\n  have b : True := trivial\n  trivial"
    assert analyze_proof(proof).local_dependency_depth == 1


def test_counts_comment_segments_separately_by_kind():
    source = "/-- doc -/\n/- block -/\ntheorem t : True := by\n  -- line one\n  trivial -- line two"
    structure = analyze_proof(source)
    assert structure.doc_comments == 1
    assert structure.block_comments == 1
    assert structure.line_comments == 2
    assert structure.comment_segments == 4
    assert structure.comment_chars > 0
    assert 0 < structure.comment_ratio < 1


def test_counts_cited_library_lemmas():
    structure = analyze_proof("by exact Nat.succ_le_of_lt (Nat.lt_of_lt_of_le h hle)")
    assert structure.cited_lemmas >= 2


def test_detects_term_mode_proofs():
    assert analyze_proof("Nat.le_refl n").term_mode
    assert not analyze_proof("by simp").term_mode


def test_measures_nesting_and_branching():
    structure = analyze_proof("by\n  rcases h with ⟨a, b⟩\n  by_cases hx : a = b\n  · simp\n  · omega")
    assert structure.max_nesting_depth >= 1
    assert structure.branch_points >= 3


def test_empty_proof_analyzes_without_error():
    structure = analyze_proof("")
    assert structure.chars == 0
    assert structure.comment_ratio == 0.0


# -- repetition -------------------------------------------------------


def test_detects_repeated_lines():
    proof = "by\n  simp\n  simp\n  simp\n  simp"
    structure = analyze_proof(proof)
    assert structure.line_repetition_rate > 0.5
    assert structure.max_consecutive_duplicate_lines == 4
    assert structure.tactic_repetition_rate > 0.5


def test_a_varied_proof_has_low_repetition():
    structure = analyze_proof(PROOF)
    assert structure.line_repetition_rate == pytest.approx(0.0)
    assert structure.max_consecutive_duplicate_lines == 0


def test_repeated_trigram_rate_catches_degenerate_loops():
    degenerate = analyze_proof("by " + "simp [foo] ; " * 20)
    varied = analyze_proof(PROOF)
    assert degenerate.repeated_trigram_rate > varied.repeated_trigram_rate


# -- sample duplication ----------------------------------------------


def test_identical_samples_are_detected():
    dup = sample_duplication({"a": ["same", "same", "same"]})
    assert dup.tasks_all_identical == 1
    assert dup.mean_duplicate_fraction == pytest.approx(2 / 3)
    assert dup.undermines_pass_at_k


def test_distinct_samples_are_clean():
    dup = sample_duplication({"a": ["one", "two", "three"]})
    assert dup.mean_duplicate_fraction == 0.0
    assert not dup.undermines_pass_at_k


def test_single_sample_tasks_are_skipped():
    dup = sample_duplication({"a": ["only"]})
    assert dup.tasks_with_multiple_samples == 0


# -- distributions ---------------------------------------------------


def test_distribution_reports_standard_statistics():
    dist = distribution([1, 2, 3, 4, 100])
    assert dist.count == 5
    assert dist.median == 3
    assert dist.maximum == 100
    assert dist.total == 110
    # The point of reporting both: the mean is dragged by the outlier.
    assert dist.mean > dist.median


def test_empty_distribution_is_zeroed_not_an_error():
    assert distribution([]).count == 0


def test_aggregate_structure_splits_by_outcome():
    ok = analyze_proof("by simp").to_dict()
    bad = analyze_proof(PROOF).to_dict()
    stats = aggregate_structure([(ok, True, True), (bad, False, True)])
    assert stats.proofs == 2
    assert stats.verified_metrics["code_lines"].mean < stats.failed_metrics["code_lines"].mean


# -- wired into the summary ------------------------------------------


def test_summary_carries_tactics_and_structure():
    results = [
        VerificationResult(
            task_id="a",
            attempt_id="a#0",
            backend="mock",
            status=Status.VERIFIED,
            tactics=("simp", "omega"),
            structure=analyze_proof("by simp\n  omega").to_dict(),
        ),
        VerificationResult(
            task_id="b",
            attempt_id="b#0",
            backend="mock",
            status=Status.FAILED,
            tactics=("nlinarith",),
            structure=analyze_proof(PROOF).to_dict(),
        ),
    ]
    summary = summarize(results, ks=(1,))
    assert summary.tactics.total["simp"] == 1
    assert summary.structure.proofs == 2
    text = summary.format_text(include_tactics=True)
    assert "tactic frequency" in text
    assert "proof structure" in text
    assert "tactics" in summary.to_dict()


def test_failed_attempts_are_measured_too():
    # Explicitly: metrics are recorded for failures, not only successes.
    from ftp_eval import ProofAttempt, ProofTask, create

    backend = create("mock")
    task = ProofTask(task_id="t", formal_statement="theorem t : True := by")
    result = backend.verify(task, ProofAttempt(task_id="t", proof=" simp MOCK_FAIL"))
    assert result.status is Status.FAILED
    assert result.tactics == ("simp",)
    assert result.structure["chars"] > 0


def test_result_round_trips_tactics_and_structure():
    original = VerificationResult(
        task_id="a",
        attempt_id="a#0",
        backend="mock",
        status=Status.VERIFIED,
        tactics=("simp",),
        structure={"chars": 7, "lines": 1},
    )
    restored = VerificationResult.from_dict(original.to_dict())
    assert restored.tactics == ("simp",)
    assert restored.structure["chars"] == 7
