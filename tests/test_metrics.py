"""Scoring tests, including the cases where a naive pass@k is wrong."""

from __future__ import annotations

import pytest

from ftp_eval import ErrorKind, Status, VerificationResult, estimate_pass_at_k, pass_at_k, summarize
from ftp_eval.types import SoundnessReport


def result(task_id: str, status: Status, *, sample: int = 0, split: str | None = None, **kw):
    return VerificationResult(
        task_id=task_id,
        attempt_id="%s#%d" % (task_id, sample),
        backend="mock",
        status=status,
        sample_index=sample,
        split=split,
        **kw,
    )


# -- the estimator ----------------------------------------------------


def test_estimator_matches_closed_form():
    # 1 - C(n-c, k)/C(n, k) for n=10, c=2, k=3 -> 1 - C(8,3)/C(10,3)
    assert estimate_pass_at_k(10, 2, 3) == pytest.approx(1 - 56 / 120)


def test_all_correct_is_one_and_none_correct_is_zero():
    assert estimate_pass_at_k(5, 5, 1) == 1.0
    assert estimate_pass_at_k(5, 0, 1) == 0.0


def test_pass_at_1_is_just_the_sample_mean():
    assert estimate_pass_at_k(4, 1, 1) == pytest.approx(0.25)


def test_estimator_is_below_the_naive_any_passed_figure():
    # The whole reason for the unbiased estimator: with 10 samples and 1
    # success, "any of my samples passed" would report 100% for pass@1.
    assert estimate_pass_at_k(10, 1, 1) == pytest.approx(0.1)


def test_asking_for_more_samples_than_exist_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="needs at least"):
        estimate_pass_at_k(2, 1, 5)


@pytest.mark.parametrize("n,c,k", [(0, 0, 1), (5, 6, 1), (5, -1, 1), (5, 1, 0)])
def test_estimator_rejects_impossible_inputs(n, c, k):
    with pytest.raises(ValueError):
        estimate_pass_at_k(n, c, k)


# -- aggregation ------------------------------------------------------


def test_pass_at_k_skips_tasks_with_too_few_samples():
    results = [
        result("a", Status.VERIFIED, sample=0),
        result("a", Status.FAILED, sample=1),
        result("b", Status.FAILED, sample=0),  # only 1 sample
    ]
    # pass@2 is computable for "a" only; "b" is dropped rather than
    # padded with failures, which would report 0.25 instead of 0.5.
    assert pass_at_k(results, 2) == pytest.approx(1.0)
    assert pass_at_k(results, 1) == pytest.approx(0.25)


def test_pass_at_k_returns_none_when_nothing_is_computable():
    assert pass_at_k([result("a", Status.FAILED)], 4) is None


def test_harness_errors_are_excluded_from_the_denominator():
    results = [
        result("a", Status.VERIFIED),
        result("b", Status.ERROR, error_kind=ErrorKind.TOOLCHAIN),
        result("c", Status.SKIPPED),
    ]
    summary = summarize(results, ks=(1,))
    # Only task "a" was actually scoreable, so pass@1 is 1.0 -- and the
    # summary says loudly that the run was not clean.
    assert summary.scoreable_attempts == 1
    assert summary.pass_at[1] == pytest.approx(1.0)
    assert summary.errored == 1
    assert not summary.integrity_ok
    assert "WARNING" in summary.format_text()


def test_rejected_attempts_count_as_failures_not_passes():
    results = [
        result("a", Status.REJECTED, soundness=SoundnessReport(False, ("used sorry",))),
        result("b", Status.VERIFIED),
    ]
    summary = summarize(results, ks=(1,))
    assert summary.verified == 1
    assert summary.rejected == 1
    assert summary.scoreable_attempts == 2
    assert summary.pass_at[1] == pytest.approx(0.5)
    assert summary.soundness_violations == {"used sorry": 1}
    assert not summary.integrity_ok
    assert "SOUNDNESS" in summary.format_text()


def test_solve_rate_is_per_task_not_per_attempt():
    results = [
        result("a", Status.VERIFIED, sample=0),
        result("a", Status.FAILED, sample=1),
        result("b", Status.FAILED, sample=0),
        result("b", Status.FAILED, sample=1),
    ]
    summary = summarize(results, ks=(1,))
    assert summary.solved_tasks == 1
    assert summary.solve_rate == pytest.approx(0.5)
    assert summary.attempt_pass_rate == pytest.approx(0.25)


def test_summary_breaks_down_by_split():
    results = [
        result("a", Status.VERIFIED, split="valid"),
        result("b", Status.FAILED, split="test"),
    ]
    summary = summarize(results, ks=(1,))
    assert summary.by_split["valid"]["solve_rate"] == pytest.approx(1.0)
    assert summary.by_split["test"]["solve_rate"] == pytest.approx(0.0)


def test_empty_input_summarizes_without_dividing_by_zero():
    summary = summarize([], ks=(1, 5))
    assert summary.tasks == 0
    assert summary.solve_rate == 0.0
    assert summary.pass_at == {}


def test_summary_round_trips_through_json():
    import json

    summary = summarize([result("a", Status.VERIFIED)], ks=(1,))
    assert json.loads(json.dumps(summary.to_dict()))["counts"]["verified"] == 1
