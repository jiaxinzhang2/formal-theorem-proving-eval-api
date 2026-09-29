"""The HTTP verifier maps explicit responses and refuses unknown formats."""
from __future__ import annotations

import pytest
from ftp_eval.backends.axle import AxleVerifier
from ftp_eval.backends.types import Status
from ftp_eval.backends.verifier import VerifierError


def test_http_backend_refuses_to_guess_an_unrecognized_response():
    # Mis-mapping a field would silently report failures for accepted
    # proofs, so an unknown shape must raise rather than default.
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    with pytest.raises(VerifierError, match="response_map"):
        backend._interpret({"unexpected": True}, 200)


def test_http_backend_maps_a_boolean_ok_field():
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    assert backend._interpret({"verified": True}, 200).status is Status.VERIFIED
    assert backend._interpret({"verified": False}, 200).status is Status.FAILED


def test_http_backend_maps_a_custom_field_name():
    backend = AxleVerifier(
        url="https://example.invalid/verify", api_key="k", response_map={"ok": "proved"}
    )
    assert backend._interpret({"proved": True}, 200).status is Status.VERIFIED


def test_http_backend_recognizes_a_remote_timeout():
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    verdict = backend._interpret({"verified": False, "error": "verification timed out"}, 200)
    assert verdict.status is Status.TIMEOUT


def test_http_backend_parses_diagnostic_objects():
    backend = AxleVerifier(url="https://example.invalid/verify", api_key="k")
    verdict = backend._interpret(
        {"verified": False, "messages": [{"message": "unsolved goals", "line": 4}]}, 200
    )
    assert verdict.diagnostics[0].line == 4
    assert verdict.error_kind.value == "unsolved_goals"


def test_http_backend_is_unavailable_without_configuration():
    assert not AxleVerifier(url="", api_key="").info().available
    assert not AxleVerifier(url="https://example.invalid", api_key="").info().available
