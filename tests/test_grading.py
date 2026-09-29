"""The three-stage grading pipeline and its artifacts.

The contract under test is in ``ftp_eval.spec``: stages have a uniform
shape, run in declared order, stop early, and never raise. Much of what a
contest setter depends on is about *not* losing information -- misfiled
submissions, unverified toolchains, unsourced problems -- so that is tested
as carefully as the grading itself.
"""

from __future__ import annotations

import json

import pytest

from ftp_eval import ProofAttempt, ProofTask, Status, create
from ftp_eval.proving.analysis.modes import FailureMode
from ftp_eval.proving.grading import grade_contest, load_problem_set, load_submissions
from ftp_eval.proving.grading.stages import Stage
from ftp_eval.proving.matching import MismatchKind
from ftp_eval.proving.grading.metadata import (
    BenchmarkManifest,
    parse_problem_metadata,
    reconcile_toolchain,
)
from ftp_eval.proving.grading.pipeline import STAGES, CompileStage, MatchStage, run_stages
from ftp_eval.spec import StageContext, StageId, StageStatus

PROBLEM = """\
module
public import FormalConjecturesUtil

/-!
# P001
- mathdb_id: 392323
- source: https://example.org/paper
- difficulty: textbook
-/

namespace Demo

abbrev IsGood (n : ℕ) : Prop := 0 < n

@[category research open, AMS 11]
theorem demo_thm : IsLeast { n | IsGood n } answer(sorry) := by
  sorry

end Demo
"""

GOOD = PROBLEM.replace("answer(sorry)", "answer(1)").replace(
    ":= by\n  sorry", ":= by\n  exact ⟨by decide, by decide⟩"
)
GUTTED = GOOD.replace(
    "abbrev IsGood (n : ℕ) : Prop := 0 < n", "abbrev IsGood (n : ℕ) : Prop := True"
)
#: Asserts the goal as an axiom instead of proving it. Stage 1 refuses it,
#: so it never reaches a prover -- which is exactly why its metrics have to
#: be collected during stage 1 or not at all.
HACKED = GOOD.replace(
    "@[category research open, AMS 11]",
    "axiom cheat : IsLeast { n | IsGood n } 1\n\n@[category research open, AMS 11]",
)


@pytest.fixture
def benchmark(tmp_path):
    problems = tmp_path / "problems"
    problems.mkdir()
    (problems / "P001.lean").write_text(PROBLEM, encoding="utf-8")
    (problems / "P002.lean").write_text(
        PROBLEM.replace("demo_thm", "second_thm").replace("# P001", "# P002"),
        encoding="utf-8",
    )
    (tmp_path / "benchmark.json").write_text(
        json.dumps({"name": "t", "toolchain": {"lean": "v4.15.0", "mathlib_rev": "abc1234"}}),
        encoding="utf-8",
    )
    submissions = tmp_path / "submissions"
    (submissions / "alice").mkdir(parents=True)
    (submissions / "alice" / "P001.lean").write_text(GOOD, encoding="utf-8")
    (submissions / "bob").mkdir()
    (submissions / "bob" / "P001.lean").write_text(GUTTED, encoding="utf-8")
    (submissions / "bob" / "P999.lean").write_text("-- misfiled\n", encoding="utf-8")
    return tmp_path


def load(benchmark):
    problem_set = load_problem_set(benchmark / "problems")
    return problem_set, load_submissions(benchmark / "submissions", problem_set)


# -- the stage contract ------------------------------------------------


def test_stages_declare_their_own_order():
    # Ordering is data on the stage, not control flow in a driver.
    assert [s.id for s in STAGES] == [StageId.MATCH, StageId.COMPILE]
    assert MatchStage().requires is None
    assert CompileStage().requires is StageId.MATCH
    assert (StageId.MATCH.number, StageId.COMPILE.number) == (1, 2)


def test_compile_is_skipped_when_match_failed():
    """Stage 2 must not see an answer that does not state the theorem.

    A compile that *succeeds* on the wrong theorem reads like a pass, which
    is the confusion this pipeline exists to remove.
    """
    results = run_stages(
        StageContext(problem_id="P001", problem_source=PROBLEM, answer_source=GUTTED)
    )
    assert results[StageId.MATCH].status is StageStatus.FAILED
    assert StageId.COMPILE not in results


def test_compile_records_why_it_could_not_run():
    # Not the same as failing, and never collapsed into it.
    results = run_stages(
        StageContext(problem_id="P001", problem_source=PROBLEM, answer_source=GOOD)
    )
    assert results[StageId.MATCH].status is StageStatus.PASSED
    assert results[StageId.COMPILE].status is StageStatus.NOT_RUN
    assert "no prover" in results[StageId.COMPILE].detail


def test_a_stage_never_raises_on_hostile_input():
    results = run_stages(
        StageContext(problem_id="P001", problem_source="not lean at all", answer_source="\x00")
    )
    assert results[StageId.MATCH].status in (StageStatus.FAILED, StageStatus.NOT_RUN)


def test_stage_results_round_trip():
    results = run_stages(
        StageContext(problem_id="P001", problem_source=PROBLEM, answer_source=GOOD)
    )
    payload = json.loads(json.dumps(results[StageId.MATCH].to_dict()))
    assert payload["stage"] == "match"
    assert payload["stage_number"] == 1


def test_not_run_is_not_conclusive():
    assert StageStatus.PASSED.conclusive
    assert StageStatus.FAILED.conclusive
    assert not StageStatus.NOT_RUN.conclusive


# -- loading -----------------------------------------------------------


def test_problem_set_reads_ids_metadata_and_manifest(benchmark):
    problem_set, _ = load(benchmark)
    assert problem_set.ids() == ("P001", "P002")
    metadata = problem_set.metadata["P001"]
    assert metadata.mathdb_id == "392323"
    assert metadata.theorem_name == "Demo.demo_thm"
    assert metadata.answer_holes == 1
    assert metadata.difficulty == "textbook"
    assert problem_set.manifest.lean == "v4.15.0"


def test_a_misfiled_submission_is_reported_not_dropped(benchmark):
    """Work that would otherwise vanish with nobody noticing."""
    _, submissions = load(benchmark)
    by_name = {s.participant: s for s in submissions}
    assert by_name["bob"].unrecognized == ("P999.lean",)
    assert "P999" not in by_name["bob"].answers


def test_metadata_keeps_keys_it_does_not_recognize():
    metadata = parse_problem_metadata(
        "/-!\n- mathdb_id: 1\n- reviewer_initials: ab\n-/\ntheorem t : True := by sorry"
    )
    assert metadata.mathdb_id == "1"
    assert metadata.extra["reviewer_initials"] == "ab"


def test_a_problem_without_metadata_still_loads(tmp_path):
    problems = tmp_path / "problems"
    problems.mkdir()
    (problems / "P001.lean").write_text("theorem t : True := by sorry\n", encoding="utf-8")
    problem_set = load_problem_set(problems)
    assert problem_set.without_provenance() == ("P001",)


# -- grading -----------------------------------------------------------


def test_grading_separates_solved_from_refused(benchmark):
    result = grade_contest(*load(benchmark))
    by_name = {p.participant: p for p in result.participants}
    assert by_name["alice"].solved == 1
    assert by_name["bob"].solved == 0
    assert by_name["bob"].rejected == 1
    # A gutted definition is worth a human look; a missing proof would not be.
    assert len(by_name["bob"].flagged) == 1


def test_not_attempted_is_distinct_from_wrong(benchmark):
    result = grade_contest(*load(benchmark))
    alice = next(p for p in result.participants if p.participant == "alice")
    assert alice.attempted == 1
    assert alice.not_attempted == 1  # P002 never submitted


def test_statistics_include_answers_refused_at_stage_one(benchmark):
    """Stage 3 takes everything, or it could not report why answers died."""
    stats = grade_contest(*load(benchmark)).statistics
    assert stats.answers == 2
    assert stats.refused_at["match"] == 1
    assert stats.reasons


def test_unsolved_and_unattempted_problems_are_named(benchmark):
    stats = grade_contest(*load(benchmark)).statistics
    assert "P002" in stats.unsolved_problems
    assert "P002" in stats.unattempted_problems


def test_an_unverified_toolchain_is_reported(benchmark):
    """Silence here would read as agreement."""
    result = grade_contest(*load(benchmark))
    assert any("not verified" in w for w in result.toolchain_warnings)
    assert "TOOLCHAIN" in result.format_text()


def test_a_missing_toolchain_declaration_is_reported():
    warnings = reconcile_toolchain(BenchmarkManifest(), {"available": True})
    assert warnings and "does not declare a toolchain" in warnings[0]


def test_a_toolchain_mismatch_is_reported():
    warnings = reconcile_toolchain(
        BenchmarkManifest(lean="v4.15.0", mathlib_rev="abc12345"),
        {"available": True, "lean_toolchain": "v4.9.0", "mathlib_rev": "ffffffff"},
    )
    assert len(warnings) == 2


def test_solved_without_a_prover_says_it_was_not_compiled(benchmark):
    text = grade_contest(*load(benchmark)).format_text()
    assert "stage 2 skipped" in text
    assert "NOT compiled" in text


# -- artifacts ---------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "run.json",
        "problems.tsv",
        "problems.json",
        "leaderboard.tsv",
        "summary.txt",
        "summary.json",
        "1-match/all.jsonl",
        "1-match/refused.jsonl",
        "2-compile/all.jsonl",
        "3-report/by-problem.tsv",
        "3-report/by-participant.tsv",
        "3-report/reasons.tsv",
        "answers/alice/P001.json",
        "answers/bob/P001.json",
    ],
)
def test_the_results_directory_records_every_stage(benchmark, tmp_path, relative):
    result = grade_contest(
        *load(benchmark), output_dir=tmp_path / "results", run_id="r1"
    )
    assert (result.run_directory / relative).is_file(), relative


def test_the_manifest_records_declared_and_observed_toolchains(benchmark, tmp_path):
    result = grade_contest(*load(benchmark), output_dir=tmp_path / "results", run_id="r1")
    manifest = json.loads((result.run_directory / "run.json").read_text(encoding="utf-8"))
    # Both, side by side: recording only the claim would hide a mismatch.
    assert manifest["toolchain"]["declared"]["lean"] == "v4.15.0"
    assert manifest["toolchain"]["observed"]["available"] is False
    assert manifest["toolchain"]["warnings"]
    assert manifest["participants"] == ["alice", "bob"]


def test_problem_hashes_and_provenance_are_recorded(benchmark, tmp_path):
    result = grade_contest(*load(benchmark), output_dir=tmp_path / "results", run_id="r1")
    problems = json.loads(
        (result.run_directory / "problems.json").read_text(encoding="utf-8")
    )
    # Which version of the problem set was graded is a fact, not a memory.
    assert len(problems["P001"]["sha256"]) == 64
    assert problems["P001"]["mathdb_id"] == "392323"


def test_the_refused_stream_holds_only_refusals(benchmark, tmp_path):
    result = grade_contest(*load(benchmark), output_dir=tmp_path / "results", run_id="r1")
    lines = (
        (result.run_directory / "1-match/refused.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    refused = [json.loads(line) for line in lines if line.strip()]
    assert [r["participant"] for r in refused] == ["bob"]


def test_per_answer_records_carry_all_three_stages(benchmark, tmp_path):
    result = grade_contest(*load(benchmark), output_dir=tmp_path / "results", run_id="r1")
    record = json.loads(
        (result.run_directory / "answers/bob/P001.json").read_text(encoding="utf-8")
    )
    assert record["failed_at"] == "match"
    assert record["match"]["status"] == "mismatched"
    assert record["compile"]["status"] == "not_run"
    assert record["reason"]


def test_reusing_a_run_id_overwrites_in_place(benchmark, tmp_path):
    # Expressible on purpose, so a re-grade of the same run is possible; a
    # fresh UTC timestamp is the default precisely so it is opt-in.
    problem_set, submissions = load(benchmark)
    first = grade_contest(problem_set, submissions, output_dir=tmp_path / "r", run_id="x")
    second = grade_contest(problem_set, submissions, output_dir=tmp_path / "r", run_id="x")
    assert first.run_directory == second.run_directory


# -- metrics reach stage 3 --------------------------------------------


def test_every_graded_answer_carries_metrics_including_refused_ones(benchmark):
    """Grading used to record pass/fail and nothing else.

    Stage 2 calls ``verifier.probe()``, and the metrics were computed
    inside ``verify()``, which grading never calls -- so a contest run left
    stage 3 with no material at all. They are collected for refused
    answers too: those never reach a prover, so nothing else would record
    their shape, and they are where the patterns are.
    """
    problem_set, submissions = load(benchmark)
    result = grade_contest(problem_set, submissions, verifier=create("mock"))

    answers = [a for p in result.participants for a in p.grades]
    assert answers, "the fixture graded nothing"
    for answer in answers:
        assert answer.structure, "%s/%s has no structural metrics" % (
            answer.participant,
            answer.problem_id,
        )
        assert "chars" in answer.structure
        # Exactly one of the two, never both and never neither.
        assert bool(answer.success_mode) != bool(answer.failure_mode)

    refused = [a for a in answers if a.failed_at is Stage.MATCH]
    assert refused, "the fixture has no stage-1 refusal to check"
    for answer in refused:
        assert answer.structure, "a refused answer was left unmeasured"


def test_reward_hacking_gets_its_own_failure_mode(benchmark):
    (benchmark / "submissions" / "carol").mkdir()
    (benchmark / "submissions" / "carol" / "P001.lean").write_text(HACKED, encoding="utf-8")
    problem_set, submissions = load(benchmark)
    result = grade_contest(problem_set, submissions, verifier=create("mock"))

    hacked = [
        a
        for p in result.participants
        for a in p.grades
        if a.match and any(m.kind is MismatchKind.REWARD_HACKING for m in a.match.mismatches)
    ]
    assert hacked, "the fixture has no reward-hacking answer"
    for answer in hacked:
        assert answer.failure_mode == FailureMode.REWARD_HACKING.value


def test_metrics_survive_the_json_record(benchmark, tmp_path):
    problem_set, submissions = load(benchmark)
    grade_contest(
        problem_set,
        submissions,
        verifier=create("mock"),
        output_dir=tmp_path / "out",
        run_id="r",
    )
    record = json.loads(
        (tmp_path / "out" / "r" / "answers" / "alice" / "P001.json").read_text(encoding="utf-8")
    )
    assert record["metrics"]["structure"]["chars"] > 0
    assert isinstance(record["metrics"]["tactics"], list)


def test_a_backend_verdict_carries_no_metrics_of_its_own():
    """The separation that made the above possible.

    A backend reports what the prover said. Deciding what to measure about
    a proof is this API's question, so it happens in
    ``proving.analysis.measure``. If metrics leak back into ``verify()``,
    they get computed on the one path grading does not use.
    """
    backend = create("mock")
    task = ProofTask(task_id="t", formal_statement="theorem t : True := by")
    raw = backend.verify(task, ProofAttempt(task_id="t", proof=" simp MOCK_PASS"))
    assert raw.status is Status.VERIFIED
    assert raw.tactics == ()
    assert raw.structure == {}
    assert raw.success_mode is None and raw.failure_mode is None
