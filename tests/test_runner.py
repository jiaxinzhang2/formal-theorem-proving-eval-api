"""End-to-end runner behaviour: assembly, streaming, caching, resume."""

from __future__ import annotations

import json

from ftp_eval import (
    Assembly,
    EvalRunner,
    ProofAttempt,
    ProofTask,
    RunConfig,
    Status,
    create,
    load_results,
)
from ftp_eval.verifier import assemble_source


def mk_task(task_id="t1", **kw):
    kw.setdefault("header", "import Mathlib")
    kw.setdefault("formal_statement", "theorem %s : True := by" % task_id)
    return ProofTask(task_id=task_id, **kw)


# -- assembly ---------------------------------------------------------


def test_continue_statement_joins_header_statement_and_proof():
    task = mk_task()
    source = assemble_source(task, ProofAttempt(task_id="t1", proof=" trivial"))
    assert source.startswith("import Mathlib")
    assert "theorem t1 : True := by trivial" in source


def test_continue_statement_does_not_double_the_assignment_operator():
    task = mk_task(formal_statement="theorem t1 : True :=")
    source = assemble_source(task, ProofAttempt(task_id="t1", proof=":= by trivial"))
    assert ":= :=" not in source
    assert "theorem t1 : True := by trivial" in source


def test_continue_statement_respects_a_proof_that_starts_on_a_new_line():
    # Indentation is significant in tactic blocks, so a model that opens
    # with a newline keeps its own layout.
    task = mk_task()
    source = assemble_source(task, ProofAttempt(task_id="t1", proof="\n  intro h\n  exact h"))
    assert "theorem t1 : True := by\n  intro h\n  exact h" in source


def test_continue_statement_preserves_internal_newlines():
    task = mk_task()
    source = assemble_source(task, ProofAttempt(task_id="t1", proof=" intro h\n  exact h"))
    assert "theorem t1 : True := by intro h\n  exact h" in source


def test_full_file_mode_passes_the_attempt_through_untouched():
    task = mk_task(assembly=Assembly.FULL_FILE)
    source = assemble_source(task, ProofAttempt(task_id="t1", proof="whatever I want"))
    assert source == "whatever I want"


# -- runner -----------------------------------------------------------


def test_runner_streams_results_to_disk_as_they_land(tmp_path):
    tasks = [mk_task("t%d" % i) for i in range(5)]
    attempts = [ProofAttempt(task_id="t%d" % i, proof=" MOCK_PASS") for i in range(5)]
    out = tmp_path / "results.jsonl"
    seen: list[int] = []

    runner = EvalRunner(create("mock"), RunConfig(), on_progress=lambda e: seen.append(e.index))
    for _ in runner.iter_run(tasks, attempts, out_path=out):
        # The file is readable mid-run, not only at the end.
        assert out.exists()
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == len(seen)

    assert seen == [1, 2, 3, 4, 5]
    assert all(r.status is Status.VERIFIED for r in load_results(out))


def test_task_with_no_attempt_is_reported_not_dropped(tmp_path):
    # Quietly shrinking the denominator would inflate every score.
    tasks = [mk_task("t1"), mk_task("t2")]
    attempts = [ProofAttempt(task_id="t1", proof=" MOCK_PASS")]
    results = EvalRunner(create("mock")).run(tasks, attempts)
    by_id = {r.task_id: r for r in results}
    assert by_id["t1"].status is Status.VERIFIED
    assert by_id["t2"].status is Status.SKIPPED


def test_backend_exception_becomes_an_error_row_and_the_run_continues():
    tasks = [mk_task("t1"), mk_task("t2")]
    attempts = [
        ProofAttempt(task_id="t1", proof=" MOCK_ERROR"),
        ProofAttempt(task_id="t2", proof=" MOCK_PASS"),
    ]
    results = EvalRunner(create("mock")).run(tasks, attempts)
    by_id = {r.task_id: r for r in results}
    assert by_id["t1"].status is Status.ERROR
    assert by_id["t2"].status is Status.VERIFIED


def test_run_aborts_after_too_many_consecutive_errors():
    tasks = [mk_task("t%d" % i) for i in range(10)]
    attempts = [ProofAttempt(task_id="t%d" % i, proof=" MOCK_ERROR") for i in range(10)]
    config = RunConfig(max_consecutive_errors=3)
    results = EvalRunner(create("mock"), config).run(tasks, attempts)
    assert len(results) == 3


def test_empty_attempt_is_a_model_failure_not_a_harness_error():
    results = EvalRunner(create("mock")).run(
        [mk_task("t1")], [ProofAttempt(task_id="t1", proof="   ")]
    )
    assert results[0].status is Status.FAILED
    assert results[0].error_kind.value == "incomplete"


def test_cache_returns_the_second_identical_verification(tmp_path):
    tasks = [mk_task("t1")]
    attempts = [ProofAttempt(task_id="t1", proof=" MOCK_PASS")]
    config = RunConfig(cache_dir=tmp_path / "cache")

    first = EvalRunner(create("mock"), config).run(tasks, attempts)
    second = EvalRunner(create("mock"), config).run(tasks, attempts)
    assert not first[0].cached
    assert second[0].cached
    assert second[0].status is Status.VERIFIED


def test_cache_is_invalidated_by_an_edit_to_the_statement(tmp_path):
    config = RunConfig(cache_dir=tmp_path / "cache")
    attempts = [ProofAttempt(task_id="t1", proof=" MOCK_PASS")]
    EvalRunner(create("mock"), config).run([mk_task("t1")], attempts)
    edited = mk_task("t1", formal_statement="theorem t1 : False := by")
    again = EvalRunner(create("mock"), config).run([edited], attempts)
    assert not again[0].cached


def test_errors_are_never_cached(tmp_path):
    # A broken toolchain must not poison every future run.
    config = RunConfig(cache_dir=tmp_path / "cache")
    tasks = [mk_task("t1")]
    attempts = [ProofAttempt(task_id="t1", proof=" MOCK_ERROR")]
    EvalRunner(create("mock"), config).run(tasks, attempts)
    again = EvalRunner(create("mock"), config).run(tasks, attempts)
    assert not again[0].cached


def test_resume_skips_what_is_already_on_disk(tmp_path):
    out = tmp_path / "results.jsonl"
    tasks = [mk_task("t1"), mk_task("t2")]
    attempts = [ProofAttempt(task_id=t.task_id, proof=" MOCK_PASS") for t in tasks]

    EvalRunner(create("mock")).run(tasks[:1], attempts[:1], out_path=out)
    resumed = EvalRunner(create("mock"), RunConfig(resume=True)).run(
        tasks, attempts, out_path=out
    )
    assert [r.task_id for r in resumed] == ["t2"]
    assert len(load_results(out)) == 2  # the first run's row is still there


def test_resume_survives_a_truncated_final_line(tmp_path):
    out = tmp_path / "results.jsonl"
    out.write_text(
        json.dumps({"task_id": "t1", "attempt_id": "t1#0", "backend": "mock", "status": "verified"})
        + "\n{\"task_id\": \"t2\", \"attem",
        encoding="utf-8",
    )
    tasks = [mk_task("t1"), mk_task("t2")]
    attempts = [ProofAttempt(task_id=t.task_id, proof=" MOCK_PASS") for t in tasks]
    resumed = EvalRunner(create("mock"), RunConfig(resume=True)).run(tasks, attempts, out_path=out)
    assert [r.task_id for r in resumed] == ["t2"]


def test_concurrent_run_produces_the_same_verdicts(tmp_path):
    tasks = [mk_task("t%d" % i) for i in range(12)]
    attempts = [
        ProofAttempt(task_id="t%d" % i, proof=" MOCK_PASS" if i % 2 else " MOCK_FAIL")
        for i in range(12)
    ]
    serial = {r.task_id: r.status for r in EvalRunner(create("mock")).run(tasks, attempts)}
    parallel = {
        r.task_id: r.status
        for r in EvalRunner(create("mock"), RunConfig(concurrency=4)).run(tasks, attempts)
    }
    assert serial == parallel
    assert len(parallel) == 12


def test_limit_samples_truncates_per_task():
    tasks = [mk_task("t1")]
    attempts = [ProofAttempt(task_id="t1", proof=" MOCK_PASS", sample_index=i) for i in range(5)]
    results = EvalRunner(create("mock"), RunConfig(limit_samples=2)).run(tasks, attempts)
    assert len(results) == 2


def test_language_mismatch_is_skipped_rather_than_scored():
    task = mk_task("t1", language="coq")
    results = EvalRunner(create("mock")).run([task], [ProofAttempt(task_id="t1", proof="x")])
    assert results[0].status is Status.SKIPPED


def test_mock_pass_rate_is_deterministic():
    tasks = [mk_task("t%d" % i) for i in range(40)]
    attempts = [ProofAttempt(task_id="t%d" % i, proof="proof %d" % i) for i in range(40)]
    a = EvalRunner(create("mock", pass_rate=0.5)).run(tasks, attempts)
    b = EvalRunner(create("mock", pass_rate=0.5)).run(tasks, attempts)
    assert [r.status for r in a] == [r.status for r in b]
    assert 0 < sum(r.verified for r in a) < 40


def test_task_level_timeout_overrides_the_run_default():
    task = mk_task("t1", timeout_s=0.01)
    backend = create("mock", latency_s=5.0)
    # The mock sleeps min(latency, budget), so the task's budget wins.
    result = backend.verify(task, ProofAttempt(task_id="t1", proof=" MOCK_PASS"))
    assert result.wall_time_s < 1.0
