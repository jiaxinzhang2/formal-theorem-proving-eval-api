"""ftp_eval -- a unified API for evaluating formal theorem-proving models.

One interface, several provers. Write your evaluation against
:class:`~ftp_eval.verifier.Verifier` and swap Lean 4 for a remote service
without touching your scoring code.

    from ftp_eval import ProofTask, ProofAttempt, create, EvalRunner

    verifier = create("lean4", project_dir="~/mathlib-project")
    task = ProofTask(task_id="t1", header="import Mathlib",
                     formal_statement="theorem t1 (n : Nat) : n + 0 = n := by")
    attempt = ProofAttempt(task_id="t1", proof=" simp")
    print(verifier.verify(task, attempt).status)
"""

from __future__ import annotations

__version__ = "0.1.0"

from .dataset import (
    ResultWriter,
    load_attempts,
    load_results,
    load_tasks,
    read_jsonl,
    write_jsonl,
)
from .metrics import Summary, estimate_pass_at_k, pass_at_k, summarize
from .registry import available, create, register
from .runner import EvalRunner, ProgressEvent, ResultCache, RunConfig
from .types import (
    Assembly,
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProofAttempt,
    ProofTask,
    Severity,
    SoundnessReport,
    Status,
    VerificationResult,
)
from .verifier import (
    BackendUnavailable,
    RawVerdict,
    Verifier,
    VerifierError,
    assemble_source,
    screen_soundness,
)

__all__ = [
    "__version__",
    # types
    "Assembly",
    "BackendInfo",
    "Diagnostic",
    "ErrorKind",
    "ProofAttempt",
    "ProofTask",
    "Severity",
    "SoundnessReport",
    "Status",
    "VerificationResult",
    # interface
    "Verifier",
    "RawVerdict",
    "VerifierError",
    "BackendUnavailable",
    "assemble_source",
    "screen_soundness",
    # registry
    "create",
    "register",
    "available",
    # running
    "EvalRunner",
    "RunConfig",
    "ProgressEvent",
    "ResultCache",
    # io
    "load_tasks",
    "load_attempts",
    "load_results",
    "read_jsonl",
    "write_jsonl",
    "ResultWriter",
    # scoring
    "summarize",
    "Summary",
    "pass_at_k",
    "estimate_pass_at_k",
]
