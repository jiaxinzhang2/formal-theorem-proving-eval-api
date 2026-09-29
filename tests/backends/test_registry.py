"""Built-in and third-party verifier registration."""
from __future__ import annotations

import pytest
from ftp_eval import ProofAttempt, ProofTask, Status, available, create


def test_builtin_backends_are_discoverable():
    names = list(available())
    assert {"mock", "lean4", "axle"} <= set(names)


def test_unknown_backend_names_the_alternatives():
    with pytest.raises(KeyError, match="known backends"):
        create("nonexistent-prover")


def test_custom_backend_can_be_registered():
    from ftp_eval import RawVerdict, Verifier, register

    class AlwaysPasses(Verifier):
        name = "always"
        language = "lean4"

        def _verify(self, task, attempt, source, timeout_s):
            return RawVerdict.verified()

    register("always", AlwaysPasses, overwrite=True)
    assert create("always").verify(
        ProofTask(task_id="t", formal_statement="theorem t : True := by"),
        ProofAttempt(task_id="t", proof=" trivial"),
    ).status is Status.VERIFIED
