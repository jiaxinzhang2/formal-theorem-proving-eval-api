"""The published demo keeps saying what it claims to say.

`benchmarks/demo-2026` is the first thing a newcomer reads, so it is worth a
test: a demo that quietly stops matching its own README is worse than none.
Shape tests need no prover. The real-Lean test is opt-in and reproduces the
run recorded in that benchmark: three solved, one kernel refusal, one honest
non-answer, one declared axiom, one misfiled file.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from ftp_eval import evaluate_benchmark, load_problem_set, load_submissions
from ftp_eval.backends.lean4 import Lean4Verifier
from ftp_eval.proving.checking import check_interface, read_interface_problem
from ftp_eval.proving.checking.interface import InterfaceFault
from ftp_eval.proving.running.manifest import policy_from_manifest

FIXTURE = Path(__file__).resolve().parents[2] / "benchmarks" / "demo-2026"

#: participant, problem -> solved, stage that refused it
EXPECTED = {
    ("alice", "P001"): (True, None),
    ("alice", "P002"): (True, None),
    ("alice", "P003"): (True, None),
    ("bob", "P001"): (False, "kernel"),
    ("bob", "P003"): (False, "interface"),
    ("carol", "P002"): (False, "interface"),
}


def _problems():
    return load_problem_set(FIXTURE / "problems")


def test_manifest_declares_exactly_the_problems_on_disk():
    problems = _problems()
    assert set(problems.manifest.raw["problems"]) == set(problems.problems)
    # Pins are what makes a recorded result reproducible, so the demo carries
    # real ones rather than a placeholder.
    assert problems.manifest.declares_toolchain
    assert "unpinned" not in (problems.manifest.mathlib_rev or "")


def test_gold_arity_matches_each_target():
    problems = _problems()
    declared = problems.manifest.raw["problems"]
    for problem_id in sorted(problems.problems):
        interface = read_interface_problem(problems.source_for(problem_id),
                                           problem_id=problem_id,
                                           module="FtpEvalBench." + problem_id)
        gold = tuple(declared[problem_id].get("gold_arguments", ()))
        assert len(gold) == len(interface.target_parameters), problem_id
        assert declared[problem_id]["module"] == "FtpEvalBench." + problem_id


def test_every_submission_names_a_declared_problem_or_is_reported():
    problems = _problems()
    answers = load_submissions(FIXTURE / "submissions", problems)
    graded = {(a.participant, problem_id) for a in answers for problem_id in a.answers}
    assert graded == set(EXPECTED)
    # P099 is deliberately misfiled: it must be reported, never silently dropped.
    assert (FIXTURE / "submissions" / "carol" / "P099.lean").exists()
    assert "P099" not in problems.problems


@pytest.mark.parametrize("participant,problem_id,fault", [
    ("alice", "P001", None),
    ("alice", "P002", None),
    ("alice", "P003", None),
    ("bob", "P001", None),  # a true theorem, but not the one asked: only the kernel can tell
    ("bob", "P003", InterfaceFault.SOLUTION_UNPROVED),
    ("carol", "P002", InterfaceFault.REWARD_HACKING),
])
def test_interface_stage_agrees_with_the_documented_table(participant, problem_id, fault):
    problems = _problems()
    policy = policy_from_manifest(problems.manifest)
    interface = read_interface_problem(problems.source_for(problem_id),
                                       problem_id=problem_id,
                                       module="FtpEvalBench." + problem_id)
    answer = (FIXTURE / "submissions" / participant / (problem_id + ".lean")).read_text(encoding="utf-8")
    report = check_interface(interface, answer, allowed_imports=policy.allowed_imports)
    if fault is None:
        assert report.ok, report.format_text()
    else:
        assert [f for f, _ in report.faults] == [fault], report.format_text()
    # An unproved solution is a miss, not an accusation.
    assert report.honest_miss is (fault is InterfaceFault.SOLUTION_UNPROVED)


def test_demo_in_real_lean(tmp_path):
    project = os.environ.get("FTP_EVAL_MATHLIB_PROJECT")
    if not project:
        pytest.skip("set FTP_EVAL_MATHLIB_PROJECT to a built Mathlib Lake project")
    problems = _problems()
    answers = load_submissions(FIXTURE / "submissions", problems)
    verifier = Lean4Verifier(project_dir=project, lake=os.environ.get("FTP_EVAL_LAKE", "lake"))
    result = evaluate_benchmark(problems, answers, verifier=verifier,
                                output_dir=tmp_path, run_id="demo-2026", timeout_s=900.0)
    grades = result.grades()
    assert len(grades) == len(EXPECTED)
    for grade in grades:
        solved, refused_at = EXPECTED[(grade.participant, grade.problem_id)]
        assert grade.solved is solved, grade.to_dict()
        assert grade.refused_at == (refused_at or ""), grade.to_dict()
        if solved:
            # Only Lean's own classical foundations, and a real axiom listing.
            assert grade.build and grade.build.axioms is not None
            assert set(grade.build.axioms) <= {"propext", "Classical.choice", "Quot.sound"}
            assert grade.against_gold
