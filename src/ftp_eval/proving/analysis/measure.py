"""Filling in what a verdict does not know about itself.

A backend reports what the prover said: verified, failed, timed out, and
with which diagnostics. It deliberately does not measure the proof -- what
is worth measuring about an answer is this API's question, not the
toolchain's, and putting it in the backend layer both inverted the
dependency and skipped the code path grading actually uses.

So every verdict passes through here. Metrics are computed for refused and
accepted answers alike: how a proof fails is as informative as how it
succeeds, and a metric only present on successes cannot be compared
against anything.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from ...backends.types import Status, VerificationResult
from .statement_metrics import analyze_statement
from .modes import classify_failure, classify_success
from .structure import analyze_proof
from .tactics import extract_tactics

__all__ = ["measure", "proof_metrics", "metrics_for_refused"]


def proof_metrics(
    proof: str,
    formal_statement: str = "",
    language: str = "lean4",
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """The tactics used, and every structural metric, for one proof.

    The statement's own complexity travels with the proof metrics under a
    ``statement_`` prefix, so solve rate can be correlated against it --
    the closest thing to a difficulty axis available without human labels.
    """
    tactics = extract_tactics(proof, language)
    structure = analyze_proof(proof, language, tactics=tactics).to_dict()
    if formal_statement:
        structure.update(
            {
                "statement_" + key: value
                for key, value in analyze_statement(formal_statement, language)
                .to_dict()
                .items()
            }
        )
    return tactics, structure


def measure(
    result: VerificationResult,
    *,
    proof: str,
    formal_statement: str = "",
    language: str = "lean4",
) -> VerificationResult:
    """Return ``result`` with its metrics and its mode filled in.

    Both directions are classified, so a pass and a failure are described
    in equal detail.
    """
    tactics, structure = proof_metrics(proof, formal_statement, language)
    if result.status is Status.VERIFIED:
        failure_mode = None
        success_mode = classify_success(tactics, structure).value
    else:
        mode = classify_failure(
            result.status,
            result.error_kind,
            result.diagnostics,
            proof=proof,
            soundness_ok=result.soundness.ok,
        )
        failure_mode = mode.value if mode else None
        success_mode = None
    return replace(
        result,
        tactics=tactics,
        structure=structure,
        failure_mode=failure_mode,
        success_mode=success_mode,
    )


def metrics_for_refused(
    proof: str,
    formal_statement: str = "",
    language: str = "lean4",
) -> Mapping[str, Any]:
    """Metrics for an answer no prover was asked about.

    A stage-1 refusal never reaches a backend, so there is no verdict to
    fill in -- but the answer's shape is still worth recording, and the
    refused answers are where the interesting patterns are.
    """
    tactics, structure = proof_metrics(proof, formal_statement, language)
    return {"tactics": tactics, "structure": structure}
