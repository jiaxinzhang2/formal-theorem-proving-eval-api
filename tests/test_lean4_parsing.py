"""Lean log parsing and error classification.

These run without Lean installed: they test the part of the backend that
turns prover output into a verdict, which is where mistakes silently
corrupt scores.
"""

from __future__ import annotations

import pytest

from ftp_eval.proving.backends.lean4 import Lean4Verifier, classify_lean_message, parse_lean_log
from ftp_eval.shared.types import ErrorKind, Severity


def test_parses_position_and_severity():
    diags = parse_lean_log("Main.lean:12:4: error: unknown identifier 'foo'")
    assert len(diags) == 1
    d = diags[0]
    assert (d.line, d.column, d.severity) == (12, 4, Severity.ERROR)
    assert d.kind is ErrorKind.UNKNOWN_IDENTIFIER


def test_attaches_the_goal_state_to_its_error():
    log = "\n".join(
        [
            "Main.lean:8:2: error: unsolved goals",
            "n : Nat",
            "⊢ n + 0 = n",
        ]
    )
    diags = parse_lean_log(log)
    assert len(diags) == 1
    assert "⊢ n + 0 = n" in diags[0].message
    assert diags[0].kind is ErrorKind.UNSOLVED_GOALS


def test_classifies_unsolved_goals_found_only_in_the_body():
    # "error: " with the actual reason on the next line is common.
    log = "Main.lean:3:0: error: \nunsolved goals\n⊢ False"
    assert parse_lean_log(log)[0].kind is ErrorKind.UNSOLVED_GOALS


def test_warnings_are_kept_but_not_classified_as_errors():
    diags = parse_lean_log("Main.lean:1:0: warning: declaration uses 'sorry'")
    assert diags[0].severity is Severity.WARNING
    assert diags[0].kind is None


def test_multiple_errors_are_all_captured_in_order():
    log = "\n".join(
        [
            "Main.lean:1:0: error: unexpected token",
            "Main.lean:5:2: error: type mismatch",
        ]
    )
    diags = parse_lean_log(log)
    assert [d.kind for d in diags] == [ErrorKind.SYNTAX, ErrorKind.TYPE]


def test_noise_without_positions_is_ignored():
    assert parse_lean_log("Building Mathlib\nwarning: something\n") == []


@pytest.mark.parametrize(
    "message,expected",
    [
        ("unknown constant 'Nat.foo'", ErrorKind.UNKNOWN_IDENTIFIER),
        ("unsolved goals", ErrorKind.UNSOLVED_GOALS),
        ("linarith failed to find a contradiction", ErrorKind.TACTIC_FAILED),
        ("simp made no progress", ErrorKind.TACTIC_FAILED),
        ("type mismatch", ErrorKind.TYPE),
        ("failed to synthesize instance", ErrorKind.TYPE),
        ("unexpected token 'theorem'", ErrorKind.SYNTAX),
        ("maximum recursion depth has been reached", ErrorKind.RESOURCE_LIMIT),
        ("(deterministic) timeout at whnf", ErrorKind.RESOURCE_LIMIT),
        ("something nobody has seen", ErrorKind.UNKNOWN),
    ],
)
def test_classifier_table(message, expected):
    assert classify_lean_message(message) is expected


def test_unknown_identifier_wins_over_the_generic_type_label():
    # Both patterns match; the specific one is the useful one.
    assert classify_lean_message("unknown identifier, type mismatch") is ErrorKind.UNKNOWN_IDENTIFIER


# -- option injection -------------------------------------------------


def test_heartbeat_option_goes_after_the_imports():
    # Lean requires every import at the top of the file, so prepending
    # set_option would turn every attempt into a syntax error.
    backend = Lean4Verifier(max_heartbeats=1000)
    source = backend._with_options("import Mathlib\nimport Aesop\n\ntheorem t : True := by trivial")
    lines = [ln for ln in source.splitlines() if ln.strip()]
    assert lines[0].startswith("import Mathlib")
    assert lines[1].startswith("import Aesop")
    assert lines[2] == "set_option maxHeartbeats 1000"


def test_heartbeat_option_is_omitted_when_disabled():
    backend = Lean4Verifier(max_heartbeats=None)
    source = "import Mathlib\ntheorem t : True := by trivial"
    assert backend._with_options(source) == source


def test_backend_reports_unavailable_without_a_project():
    info = Lean4Verifier(project_dir=None, lake="definitely-not-a-real-binary").info()
    assert not info.available
    assert info.detail
