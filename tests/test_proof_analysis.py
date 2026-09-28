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


def _chained_proof(n: int) -> str:
    lines = ["by", "  have h0 : True := trivial"]
    lines += ["  have h%d : True := by exact h%d" % (i, i - 1) for i in range(1, n)]
    lines.append("  exact h%d" % (n - 1))
    return "\n".join(lines)


def test_dependency_depth_is_correct_on_a_long_chain():
    structure = analyze_proof(_chained_proof(200))
    assert structure.named_steps == 200
    assert structure.local_dependency_depth == 201


def test_analysis_stays_linear_in_the_number_of_steps():
    """Guard against the quadratic dependency walk coming back.

    The first implementation regex-searched every earlier step name in
    every step's justification, which measured 177ms at 400 steps and
    would be ~4.4s here. The linear version is ~30ms, so a 1.5s bound
    separates the two cleanly while leaving a wide margin for slow CI.
    A degenerate model emitting thousands of `have`s is exactly the input
    that has to stay cheap.
    """
    import time

    proof = _chained_proof(2000)
    started = time.perf_counter()
    structure = analyze_proof(proof)
    elapsed = time.perf_counter() - started
    assert structure.named_steps == 2000
    assert elapsed < 1.5, "analyze_proof took %.2fs on 2000 steps; it may be quadratic again" % elapsed


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


def test_a_bare_tactic_fragment_is_not_term_mode():
    """Under `continue_statement` the `by` is in the statement, not here.

    Judging term-mode on the absence of `by` labelled every ordinary
    tactic proof in that convention as term-mode, which then showed up as
    a bogus success-mode distribution.
    """
    assert not analyze_proof(" simp").term_mode
    assert not analyze_proof(" nlinarith [sq_nonneg n]").term_mode
    assert not analyze_proof("\n  intro h\n  omega").term_mode


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


# -- tactic sequence signal -------------------------------------------


def test_records_opening_and_closing_tactics():
    stats = tactic_stats(
        [
            (("intro", "simp", "omega"), True, True),
            (("intro", "nlinarith"), False, True),
        ]
    )
    assert stats.first_tactics == {"intro": 2}
    assert stats.closing_tactics == {"omega": 1, "nlinarith": 1}


def test_records_transitions_split_by_outcome():
    stats = tactic_stats(
        [
            (("simp", "omega"), True, True),
            (("simp", "omega"), False, True),
            (("simp", "linarith"), False, True),
        ]
    )
    assert stats.transitions == {"simp->omega": 2, "simp->linarith": 1}
    assert stats.transitions_verified == {"simp->omega": 1}
    assert stats.transition_success_rate("simp->omega") == pytest.approx(0.5)
    assert stats.transition_success_rate("simp->linarith") == pytest.approx(0.0)
    assert stats.transition_success_rate("never->seen") is None


def test_mean_position_places_tactics_in_the_proof():
    stats = tactic_stats([(("intro", "simp", "omega"), True, True)])
    assert stats.mean_position["intro"] == pytest.approx(0.0)
    assert stats.mean_position["omega"] == pytest.approx(1.0)
    assert stats.mean_position["simp"] == pytest.approx(0.5)


def test_transitions_appear_in_the_report():
    stats = tactic_stats([(("intro", "simp"), True, True)] * 3)
    text = stats.format_text()
    assert "opens with" in text
    assert "intro->simp" in text


# -- statement complexity ---------------------------------------------


def test_counts_binders_by_kind():
    from ftp_eval.proof_metrics import analyze_statement

    c = analyze_statement(
        "theorem t {α : Type} [Ring α] (x : α) (h : 0 < x) : x + 0 = x := by"
    )
    assert c.binders == 4
    assert c.implicit_binders == 1
    assert c.instance_binders == 1
    assert c.hypotheses == 1  # only `0 < x` looks like a proposition
    assert c.distinct_types == 4


def test_counts_quantifiers_and_connectives():
    from ftp_eval.proof_metrics import analyze_statement

    c = analyze_statement("theorem t : ∀ n, n = 0 ∨ 0 < n := by")
    assert c.quantifiers == 1
    assert c.connectives >= 1


def test_measures_the_conclusion_separately_from_the_binders():
    from ftp_eval.proof_metrics import analyze_statement

    simple = analyze_statement("theorem t (n : Nat) : n = n := by")
    complex_ = analyze_statement(
        "theorem t (n : Nat) : n * n + 2 * n + 1 = (n + 1) * (n + 1) := by"
    )
    assert complex_.conclusion_tokens > simple.conclusion_tokens


def test_statement_complexity_survives_an_unparseable_statement():
    from ftp_eval.proof_metrics import analyze_statement

    # Token and quantifier counts should still come through rather than
    # the whole measurement returning nothing.
    c = analyze_statement("theorem ∀∃ garbage ∧")
    assert c.total_tokens > 0
    assert c.quantifiers == 2


def test_statement_complexity_travels_with_the_result():
    from ftp_eval import ProofAttempt, ProofTask, create

    backend = create("mock")
    task = ProofTask(
        task_id="t",
        formal_statement="theorem t (n : Nat) (h : 0 < n) : n ^ 2 >= n := by",
    )
    result = backend.verify(task, ProofAttempt(task_id="t", proof=" nlinarith MOCK_PASS"))
    assert result.structure["statement_binders"] == 2
    assert result.structure["statement_hypotheses"] == 1


# -- correlation ------------------------------------------------------


def test_point_biserial_detects_a_clean_relationship():
    from ftp_eval.proof_metrics import point_biserial

    values = [1, 2, 3, 10, 11, 12]
    outcomes = [True, True, True, False, False, False]
    r = point_biserial(values, outcomes)
    assert r is not None
    assert r < -0.9  # high values go with failure


def test_point_biserial_is_none_when_undefined():
    from ftp_eval.proof_metrics import point_biserial

    assert point_biserial([1, 2], [True, False]) is None          # too few points
    assert point_biserial([5, 5, 5], [True, False, True]) is None  # no variation
    assert point_biserial([1, 2, 3], [True, True, True]) is None   # no outcome variation


def test_point_biserial_rejects_mismatched_lengths():
    from ftp_eval.proof_metrics import point_biserial

    with pytest.raises(ValueError):
        point_biserial([1, 2, 3], [True, False])


def test_correlations_are_ranked_by_magnitude():
    from ftp_eval.proof_metrics import correlate_with_success

    entries = []
    for i in range(20):
        verified = i < 10
        entries.append(
            ({"lines": 2 if verified else 40, "cited_lemmas": 3}, verified)
        )
    correlations = correlate_with_success(entries)
    assert correlations
    assert correlations[0].metric == "lines"
    assert correlations[0].strength == "strong"
    # A constant metric carries no signal and is dropped, not reported as 0.
    assert "cited_lemmas" not in {c.metric for c in correlations}


def test_correlation_report_states_the_causation_caveat():
    from ftp_eval.proof_metrics import correlate_with_success, format_correlations

    entries = [({"lines": 2 if i < 6 else 40}, i < 6) for i in range(12)]
    text = format_correlations(correlate_with_success(entries))
    assert "NOT causation" in text
    assert "task difficulty" in text


def test_empty_correlations_say_so():
    from ftp_eval.proof_metrics import format_correlations

    assert "none above" in format_correlations([])


# -- per-model and sample-position rollups ----------------------------


def _result(task_id, status, *, model=None, sample=0, structure=None):
    return VerificationResult(
        task_id=task_id,
        attempt_id="%s#%d" % (task_id, sample),
        backend="mock",
        status=status,
        model=model,
        sample_index=sample,
        structure=structure or {},
    )


def test_summary_breaks_down_by_model():
    results = [
        _result("a", Status.VERIFIED, model="strong"),
        _result("b", Status.VERIFIED, model="strong"),
        _result("a", Status.FAILED, model="weak"),
        _result("b", Status.FAILED, model="weak"),
    ]
    summary = summarize(results, ks=(1,))
    assert summary.by_model["strong"]["solve_rate"] == pytest.approx(1.0)
    assert summary.by_model["weak"]["solve_rate"] == pytest.approx(0.0)
    assert "by model:" in summary.format_text()


def test_single_model_run_gets_no_by_model_noise():
    results = [_result("a", Status.VERIFIED, model="only")]
    assert summarize(results, ks=(1,)).by_model == {}


def test_samples_to_first_success_is_one_based():
    results = [
        _result("a", Status.FAILED, sample=0),
        _result("a", Status.FAILED, sample=1),
        _result("a", Status.VERIFIED, sample=2),
    ]
    summary = summarize(results, ks=(1,))
    # Third sample succeeded, so three samples were drawn.
    assert summary.samples_to_first_success.median == pytest.approx(3.0)


def test_unsolved_tasks_do_not_enter_samples_to_first_success():
    results = [_result("a", Status.FAILED, sample=i) for i in range(3)]
    assert summarize(results, ks=(1,)).samples_to_first_success.count == 0


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
