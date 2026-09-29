"""Dependency extraction requires an exact, unique declaration listing."""
from __future__ import annotations

from ftp_eval.backends.lean_diagnostics import parse_printed_axioms


def test_axiom_listing_requires_exact_unique_name():
    clean = "'ftp_eval_target' does not depend on any axioms"
    assert parse_printed_axioms(clean, "ftp_eval_target") == []
    assert parse_printed_axioms(clean + "\n" + clean, "ftp_eval_target") is None
    assert parse_printed_axioms("'Submission.ftp_eval_target' depends on axioms: [propext]", "ftp_eval_target") is None
