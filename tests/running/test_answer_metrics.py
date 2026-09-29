"""Measure the frozen proposition rather than the surrounding problem module."""
from __future__ import annotations

from ftp_eval.proving.running.recording import grade_answer


PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"


ANSWER = "import FtpEvalBench.P001\nnamespace Submission\nvariable (k : Nat)\ntheorem solution : Problem.Target 1 := rfl\nend Submission\n"


def test_statement_metrics_measure_the_target_body():
    grade = grade_answer(PROBLEM, ANSWER, problem_id="P001", participant="alice")
    assert grade.structure["statement_conclusion_tokens"] == 3
    assert grade.structure["statement_binders"] == 1
