"""Proof inputs have valid identities and explicit assembly requirements."""
from __future__ import annotations

import pytest
from ftp_eval import ProofAttempt, ProofTask


def test_missing_task_id_is_rejected_loudly():
    with pytest.raises(ValueError, match="task_id"):
        ProofTask(task_id="", formal_statement="theorem t : True := by")


def test_continue_statement_needs_a_statement():
    with pytest.raises(ValueError, match="formal_statement"):
        ProofTask(task_id="t1", formal_statement="   ")


def test_attempt_id_defaults_to_task_and_sample():
    assert ProofAttempt(task_id="t1", proof="x", sample_index=3).attempt_id == "t1#3"
