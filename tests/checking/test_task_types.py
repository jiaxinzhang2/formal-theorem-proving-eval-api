"""One Target interface for proofs, objects, dependent functions and predicates.

Shape tests need no prover. The two opt-in tests below compile the published
fixtures in real Lean; Docker additionally requires fresh kernel replay.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest

from ftp_eval import evaluate_benchmark, load_problem_set, load_submissions
from ftp_eval.backends.lean4 import Lean4Verifier
from ftp_eval.backends.lean4_docker import Lean4DockerVerifier
from ftp_eval.backends.types import ModuleBuild, Status
from ftp_eval.proving.checking import (
    InterfaceFault, InterfaceReport, InterfaceVerdict, build_submission_modules,
    check_interface, read_interface_problem,
)
from ftp_eval.proving.checking.policy import ContestPolicy
from ftp_eval.proving.running.inputs import ProblemSet, Submission


FIXTURE = Path(__file__).resolve().parents[2] / "benchmarks" / "task-types"


@pytest.mark.parametrize("problem_id,parameters", [
    ("P001", ()), ("P002", ("m",)), ("P003", ("f",)), ("P004", ("S",)),
])
def test_task_types_use_only_target_arity(problem_id, parameters):
    problems = load_problem_set(FIXTURE / "problems")
    source = problems.source_for(problem_id)
    interface = read_interface_problem(source, problem_id=problem_id,
                                       module="FtpEvalBench." + problem_id)
    assert interface.target_parameters == parameters
    answer = (FIXTURE / "submissions" / "standard" / (problem_id + ".lean")).read_text(encoding="utf-8")
    report = check_interface(interface, answer, allowed_imports=("Init", "Std"))
    assert report.ok, report.format_text()
    modules = build_submission_modules(interface, source, answer, "FtpSubmission.Task", report.submitted_arguments)
    assert modules[0].cacheable
    assert not modules[-1].cacheable
    assert "Submission.solution" in modules[-1].source
    if parameters:
        assert modules[1].cacheable
        assert "∃ ftp_value_0, @_root_.Problem.Target ftp_value_0" in modules[1].source
        assert "⟨_, Submission.solution⟩" in modules[-1].source
        assert "Problem.Target answer" not in modules[-1].source
    else:
        assert len(modules) == 3
        assert "theorem ftp_eval_target : _root_.Problem.Target" in modules[-1].source


@pytest.mark.parametrize("problem_id", ["P002", "P003"])
def test_answer_name_is_optional_even_for_a_dependent_function(problem_id):
    source = (FIXTURE / "problems" / (problem_id + ".lean")).read_text(encoding="utf-8")
    interface = read_interface_problem(source, module="FtpEvalBench." + problem_id)
    answer = (FIXTURE / "submissions" / "alternate" / (problem_id + ".lean")).read_text(encoding="utf-8")
    assert "def answer" not in answer
    report = check_interface(interface, answer, allowed_imports=("Std",))
    assert report.ok, report.format_text()
    assert report.submitted_arguments == ("chosen",)


def test_function_gold_is_frozen_before_submission():
    source = (FIXTURE / "problems" / "P004.lean").read_text(encoding="utf-8")
    gold = "fun n : Nat => n % 77 = 38"
    interface = replace(read_interface_problem(source, module="FtpEvalBench.P004"), gold_arguments=(gold,))
    modules = build_submission_modules(interface, source, "", "FtpSubmission.Task", ())
    assert gold in modules[1].source
    assert gold not in modules[-1].source
    assert "FtpSubmission.Task" not in modules[1].source


def test_function_inputs_belong_on_answer_not_solution():
    source = (FIXTURE / "problems" / "P003.lean").read_text(encoding="utf-8")
    interface = read_interface_problem(source, module="FtpEvalBench.P003")
    answer = (FIXTURE / "submissions" / "invalid" / "P003.lean").read_text(encoding="utf-8")
    report = check_interface(interface, answer)
    assert InterfaceFault.SOLUTION_BINDERS in {fault for fault, _ in report.faults}


def test_verified_witness_is_not_reported_as_a_weaker_proof():
    # Synthetic build: tests wording only, not Lean acceptance.
    verdict = InterfaceVerdict("P002", report=InterfaceReport("P002"),
                               build=ModuleBuild(Status.VERIFIED, axioms=()),
                               axiom_audit=ContestPolicy().audit(()), against_gold=False)
    assert verdict.solved
    assert "verified witness; no fixed gold" in verdict.format_text()


def test_task_types_in_real_lean(tmp_path):
    project = os.environ.get("FTP_EVAL_LEAN_PROJECT")
    if not project:
        pytest.skip("set FTP_EVAL_LEAN_PROJECT to a built Std-capable Lake project")
    problems = load_problem_set(FIXTURE / "problems")
    answers = load_submissions(FIXTURE / "submissions", problems)
    verifier = Lean4Verifier(project_dir=project, lake=os.environ.get("FTP_EVAL_LAKE", "lake"))
    result = evaluate_benchmark(problems, answers, verifier=verifier, output_dir=tmp_path, run_id="task-types")
    grades = result.grades()
    assert len(grades) == 10
    assert all(grade.solved == (grade.participant != "invalid") for grade in grades), result.to_dict()
    for grade in grades:
        if grade.solved:
            assert grade.build and grade.build.axioms is not None
            assert grade.against_gold == (grade.problem_id == "P001")
        elif grade.problem_id == "P003":
            assert grade.refused_at == "interface"
        else:
            assert grade.refused_at == "kernel"

    # Function-valued gold follows the same trusted-Goal path. An alias of
    # the reference predicate is definitionally equal; True is a different goal.
    predicate_answer = next(answer for answer in answers if answer.participant == "standard")
    for gold, expected in [("fun n : Nat => ∃ k : Nat, n = 38 + 77 * k", True),
                           ("fun _ : Nat => True", False)]:
        raw = {**problems.manifest.raw, "problems": {"P004": {"gold_arguments": [gold]}}}
        manifest = replace(problems.manifest, raw=raw)
        fixed = ProblemSet({"P004": problems.source_for("P004")}, manifest=manifest)
        gold_result = evaluate_benchmark(fixed, [Submission("standard", {"P004": predicate_answer.answers["P004"]})],
                                         verifier=verifier)
        grade = gold_result.grades()[0]
        assert grade.solved == expected, grade.to_dict()
        assert grade.against_gold
        if not expected:
            assert grade.refused_at == "kernel"


def test_dependent_function_in_real_docker(tmp_path):
    image = os.environ.get("FTP_EVAL_DOCKER_IMAGE")
    if not image:
        pytest.skip("set FTP_EVAL_DOCKER_IMAGE to an immutable built organizer image")
    verifier = Lean4DockerVerifier(image=image, workspace=str(tmp_path / "worker"))
    assert verifier.info().available, verifier.info().detail
    problems = load_problem_set(FIXTURE / "problems")
    env = verifier.environment_metadata
    # Test-only pins observed from the selected image. Official benchmark pins
    # must be declared independently before any submissions are evaluated.
    pins = {key: env[key] for key in ("lake_manifest_sha256", "image_digest")}
    pins["lean"] = env["lean_toolchain"]
    manifest = replace(problems.manifest, lean=pins["lean"],
                       raw={**problems.manifest.raw, "toolchain": pins})
    single = ProblemSet({"P003": problems.source_for("P003")}, manifest=manifest)
    good = (FIXTURE / "submissions" / "standard" / "P003.lean").read_text(encoding="utf-8")
    wrong = "import FtpEvalBench.P003\nnamespace Submission\ntheorem solution : True := trivial\nend Submission\n"
    result = evaluate_benchmark(single, [Submission("good", {"P003": good}), Submission("wrong", {"P003": wrong})],
                                verifier=verifier, output_dir=tmp_path / "runs", run_id="dependent-function")
    accepted, rejected = result.grades()
    assert accepted.solved and not accepted.against_gold, result.to_dict()
    assert any(event["stage"] == "replay" and event["status"] == "passed" for event in accepted.stages)
    assert rejected.refused_at == "kernel" and not rejected.solved
