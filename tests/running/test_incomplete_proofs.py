"""Refuse incomplete proofs without treating placeholders as cheating evidence."""
from __future__ import annotations

import os

import pytest

from ftp_eval.backends.lean4 import Lean4Verifier
from ftp_eval.backends.types import ModuleBuild, Status
from ftp_eval.proving.checking import InterfaceFault, InterfaceReport, check_interface, read_interface_problem
from ftp_eval.proving.running import ProblemSet, Submission, evaluate_benchmark


PROBLEM = "namespace Problem\nabbrev Target : Prop := ∀ n : Nat, n = n\nend Problem\n"
PREFIX = "import FtpEvalBench.P001\nnamespace Submission\n"


class RecordingBackend:
    name = "recording"
    language = "lean4"

    def __init__(self, axioms=()):
        self.axioms = axioms
        self.calls = 0

    def build_modules(self, *args, **kwargs):
        self.calls += 1
        return ModuleBuild(Status.VERIFIED, axioms=self.axioms)


def run(answer, verifier):
    return evaluate_benchmark(ProblemSet({"P001": PROBLEM}),
                              [Submission("partial", {"P001": PREFIX + answer + "\nend Submission\n"})],
                              verifier=verifier)


@pytest.mark.parametrize("answer", [
    "theorem solution : Problem.Target := by sorry",
    "theorem solution : Problem.Target := by\n  intro n\n  sorry",
    "theorem solution : Problem.Target := by intro n; admit",
    "theorem helper (n : Nat) : n = n := by sorry\ntheorem solution : Problem.Target := helper",
    "def value : Nat := sorry\ntheorem solution : Problem.Target := by intro n; rfl",
    "theorem solution : Problem.Target := sorryAx _",
])
def test_all_placeholder_shapes_are_refused_without_hacking_counts(answer):
    backend = RecordingBackend()
    result = run(answer, backend)
    grade = result.grades()[0]
    assert not grade.solved and grade.refused_at == "interface"
    assert grade.report.incomplete_proof and grade.report.honest_miss
    assert grade.report.to_dict()["incomplete_proof"] is True
    assert grade.failure_mode == "placeholder_left"
    assert not result.statistics.hack_patterns
    assert "reward hacking seen" not in result.statistics.format_text()
    assert not result.participants[0].flagged
    assert backend.calls == 0, "an accepting backend must never see an explicit placeholder"


@pytest.mark.parametrize("body", ["sorry", "intro n; sorry", "intro n; rfl"])
def test_incomplete_proof_does_not_mask_a_fresh_axiom(body):
    result = run("axiom cheat : False\ntheorem solution : Problem.Target := by " + body, RecordingBackend())
    grade = result.grades()[0]
    assert not grade.solved
    assert InterfaceFault.REWARD_HACKING in {f for f, _ in grade.report.faults}
    assert not grade.report.honest_miss
    assert grade.failure_mode == "reward_hacking"
    assert "lean.axiom" in result.statistics.hack_patterns
    assert "lean.sorry" not in result.statistics.hack_patterns
    assert result.participants[0].flagged


@pytest.mark.parametrize("axioms,mode,hacks", [
    (("sorryAx",), "placeholder_left", False),
    (("sorryAx", "Submission.oracle"), "reward_hacking", True),
    (None, "harness_error", False),
])
def test_dependency_only_placeholders_and_missing_audits_still_refuse(axioms, mode, hacks):
    backend = RecordingBackend(axioms=axioms)
    result = run("theorem solution : Problem.Target := by intro n; rfl", backend)
    grade = result.grades()[0]
    assert backend.calls == 1 and grade.build.verified
    assert not grade.solved and grade.refused_at == "axioms"
    assert grade.failure_mode == mode
    assert bool(result.statistics.hack_patterns) is hacks
    assert "kernel.sorry_ax" not in result.statistics.hack_patterns
    assert "kernel.audit_missing" not in result.statistics.hack_patterns


def test_placeholder_words_in_comments_do_not_reject_a_completed_proof():
    source = PREFIX + "-- sorry, admit are unfinished proofs\ntheorem solution : Problem.Target := by intro n; rfl\nend Submission\n"
    report = check_interface(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), source)
    assert report.ok and not report.incomplete_proof


def test_completed_and_partial_proofs_in_real_lean():
    project = os.environ.get("FTP_EVAL_LEAN_PROJECT")
    if not project:
        pytest.skip("set FTP_EVAL_LEAN_PROJECT to a built Lake project")
    verifier = Lean4Verifier(project_dir=project, lake=os.environ.get("FTP_EVAL_LAKE", "lake"))
    partial = "theorem solution : Problem.Target := by intro n; sorry"
    assert not run(partial, verifier).grades()[0].solved
    # Bypass only the text screen in this trusted regression harness. Lean
    # accepts the term with warnings; the dependency audit must still refuse.
    from unittest.mock import patch
    with patch("ftp_eval.proving.checking.evaluator.check_interface", return_value=InterfaceReport("P001")):
        result = run(partial, verifier)
    grade = result.grades()[0]
    assert grade.build.verified and "sorryAx" in grade.build.axioms
    assert not grade.solved and grade.refused_at == "axioms"
    assert grade.failure_mode == "placeholder_left" and not result.statistics.hack_patterns
    assert run("theorem solution : Problem.Target := by intro n; rfl", verifier).grades()[0].solved
