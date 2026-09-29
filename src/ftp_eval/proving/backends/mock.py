"""A deterministic fake prover.

Exists so the pipeline -- assembly, soundness screening, concurrency,
caching, pass@k -- can be tested and demoed on a machine with no prover
installed, and so that CI covers the harness rather than Lean's build
times. It is also the reference implementation to read before writing a
real backend: it is about thirty lines of actual logic.

It decides verdicts by looking for marker strings in the proof, which
makes fixtures readable and results reproducible.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from ...types import (
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProofAttempt,
    ProofTask,
    Severity,
    Status,
)
from ..verifier import RawVerdict, Verifier, VerifierError

__all__ = ["MockVerifier"]

#: Marker -> (status, error kind). Checked in this order.
_MARKERS: tuple[tuple[str, Status, ErrorKind | None], ...] = (
    ("MOCK_TIMEOUT", Status.TIMEOUT, ErrorKind.TIMEOUT),
    ("MOCK_ERROR", Status.ERROR, ErrorKind.HARNESS),
    ("MOCK_SYNTAX_ERROR", Status.FAILED, ErrorKind.SYNTAX),
    ("MOCK_UNSOLVED", Status.FAILED, ErrorKind.UNSOLVED_GOALS),
    ("MOCK_FAIL", Status.FAILED, ErrorKind.TACTIC_FAILED),
    ("MOCK_PASS", Status.VERIFIED, None),
)


class MockVerifier(Verifier):
    """Fake prover driven by markers in the proof text.

    Verdict rules, in order:

    * an explicit ``MOCK_*`` marker wins;
    * otherwise, if ``pass_rate`` is set, a hash of the proof decides,
      giving a stable pseudo-random pass rate for load testing;
    * otherwise the attempt fails with ``UNSOLVED_GOALS``.

    Note that a proof containing ``sorry`` still comes back ``REJECTED``
    even with ``MOCK_PASS``: soundness screening happens in the base
    class, above every backend, and cannot be opted out of per-backend.
    """

    name = "mock"
    language = "lean4"
    thread_safe = True

    def __init__(
        self,
        *,
        language: str = "lean4",
        pass_rate: float | None = None,
        latency_s: float = 0.0,
        seed: str = "ftp-eval",
        **config: Any,
    ) -> None:
        super().__init__(**config)
        self.language = language
        if pass_rate is not None and not 0.0 <= pass_rate <= 1.0:
            raise ValueError("pass_rate must be in [0, 1]")
        self.pass_rate = pass_rate
        self.latency_s = max(0.0, latency_s)
        self.seed = seed

    def info(self) -> BackendInfo:
        return BackendInfo(
            name=self.name,
            language=self.language,
            available=True,
            version="mock-1",
            detail="deterministic fake prover; never proves anything",
            supports=("markers", "pass_rate", "latency"),
        )

    def _verify(
        self, task: ProofTask, attempt: ProofAttempt, source: str, timeout_s: float
    ) -> RawVerdict:
        if self.latency_s:
            time.sleep(min(self.latency_s, timeout_s))

        for marker, status, kind in _MARKERS:
            if marker in source:
                if status is Status.ERROR:
                    raise VerifierError("mock backend asked to fail via %s" % marker)
                diagnostics = (
                    ()
                    if status is Status.VERIFIED
                    else (
                        Diagnostic(
                            Severity.ERROR,
                            "mock verdict from marker %s" % marker,
                            line=1,
                            kind=kind,
                        ),
                    )
                )
                return RawVerdict(status, kind, diagnostics, {"marker": marker})

        if self.pass_rate is not None:
            digest = hashlib.sha256((self.seed + attempt.proof).encode("utf-8")).digest()
            draw = int.from_bytes(digest[:8], "big") / float(1 << 64)
            if draw < self.pass_rate:
                return RawVerdict.verified({"draw": round(draw, 6)})
            return RawVerdict.failed(
                ErrorKind.UNSOLVED_GOALS,
                [Diagnostic(Severity.ERROR, "mock: pseudo-random miss", kind=ErrorKind.UNSOLVED_GOALS)],
                {"draw": round(draw, 6)},
            )

        return RawVerdict.failed(
            ErrorKind.UNSOLVED_GOALS,
            [
                Diagnostic(
                    Severity.ERROR,
                    "mock: no MOCK_* marker found, defaulting to failure",
                    kind=ErrorKind.UNSOLVED_GOALS,
                )
            ],
        )
