"""Trusted goal expressions elaborate before importing answer-controlled declarations."""
from __future__ import annotations

from dataclasses import replace
from ftp_eval.proving.checking import read_interface_problem, build_submission_modules


PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"


ANSWER = "import FtpEvalBench.P001\nnamespace Submission\nvariable (k : Nat)\ntheorem solution : Problem.Target 1 := rfl\nend Submission\n"


def test_gold_elaborates_before_submission_import():
    problem = replace(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), gold_arguments=("2 + 2",))
    modules = build_submission_modules(problem, PROBLEM, ANSWER, "FtpSubmission.A", ("1",))
    assert [m.cacheable for m in modules] == [True, True, False, False]
    assert "Problem.Target (2 + 2)" in modules[1].source
    assert "FtpSubmission.A" not in modules[1].source
    assert "2 + 2" not in modules[-1].source
    assert "_root_.FtpEvalGoal." in modules[-1].source


def test_open_value_check_does_not_reparse_answer_expressions():
    problem = read_interface_problem(PROBLEM, module="FtpEvalBench.P001")
    modules = build_submission_modules(problem, PROBLEM, ANSWER, "FtpSubmission.A", ("my_helper",))
    assert "∃ ftp_value_0, @_root_.Problem.Target ftp_value_0" in modules[1].source
    assert "my_helper" not in modules[-1].source
    assert "⟨_, Submission.solution⟩" in modules[-1].source
