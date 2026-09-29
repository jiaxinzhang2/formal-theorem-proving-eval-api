"""Talking to a prover.

Sits above `source/` and below both APIs. Neither API owns it, which is
why it is not inside either: `proving/` asks a prover to confirm a proof,
`autoformalization/` asks the same prover to probe a statement.

    types.py     the vocabulary: what a prover is asked (ProofTask,
                 ProofAttempt, StatementTask, ProbeKind) and what it
                 answers (VerificationResult, Status, Diagnostic)
    verifier.py  the interface every backend implements, plus the
                 soundness screening backends cannot opt out of
    backends/    mock, lean4, axle

Backends are looked up by name through `../registry.py`, so nothing above
this layer needs to import a specific prover.
"""

from __future__ import annotations

__all__: list[str] = []
