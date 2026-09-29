"""ftp_eval -- a unified API for evaluating formal theorem-proving models.

A formal-proving dataset has three artifacts, so there are two links to
check, and the package is laid out along them::

    natural language  ──①──▶  formal statement  ──②──▶  formal proof
                      faithful?                 valid?

    formalizing/   ① is the formalization faithful to the problem?
      checker.py     structural probes + an LLM judge, kept distinct
      judge.py       the judge interface, a mock, consensus voting
      judges/        provider-backed judges

    proving/       ② does the proof close the goal?
      verifier.py    the interface every backend implements
      runner.py      batch execution: streaming, resume, caching
      backends/      mock, lean4, axle

    analysis/      what happened, for passes and failures alike
      tactics.py     which tactics, in what order
      structure.py   proof shape, statement complexity, correlations
      modes.py       failure modes with attribution; success modes
      scoring.py     unbiased pass@k and the Summary

    pipeline.py    both links together, with a combined verdict

Shared by all three layers, which is why they sit at the top level:

    types.py       the vocabulary every layer speaks
    soundness.py   reward-hacking detection
    comments.py    what counts as a comment, per language
    dataset.py     JSONL I/O and the three-file layout
    registry.py    backend and judge lookup by name

Everything in the public API is importable straight from ``ftp_eval``; the
module paths above matter only when extending the package.

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
    load_triplets,
    read_jsonl,
    write_jsonl,
)
from .formalizing.judge import ConsensusJudge, Judge, JudgeError, MockJudge
from .analysis.scoring import Summary, estimate_pass_at_k, pass_at_k, summarize
from .analysis.modes import (
    Attribution,
    FailureMode,
    SuccessMode,
    classify_failure,
    classify_success,
    looks_truncated,
)
from .end_to_end import EndToEndResult, EndToEndRunner, EndToEndStatus
from .analysis.structure import (
    Correlation,
    ProofStructure,
    StatementComplexity,
    analyze_proof,
    analyze_statement,
    correlate_with_success,
)
from .registry import (
    available,
    available_judges,
    create,
    create_judge,
    register,
    register_judge,
)
from .proving.runner import EvalRunner, ProgressEvent, ResultCache, RunConfig
from .soundness import HackClass, audit_axioms, screen_source
from .formalizing.checker import StatementChecker
from .analysis.tactics import extract_tactics
from .types import (
    Assembly,
    BackendInfo,
    Check,
    CheckKind,
    Diagnostic,
    ErrorKind,
    JudgeLabel,
    JudgeUsage,
    JudgeVerdict,
    ProbeKind,
    ProofAttempt,
    ProofTask,
    Severity,
    SoundnessReport,
    StatementStatus,
    StatementTask,
    StatementVerdict,
    Status,
    VerificationResult,
)
from .proving.verifier import (
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
    # reward-hacking detection
    "HackClass",
    "screen_source",
    "audit_axioms",
    # analysis
    "extract_tactics",
    "analyze_proof",
    "analyze_statement",
    "ProofStructure",
    "StatementComplexity",
    "Correlation",
    "correlate_with_success",
    # outcome classification, for passes and failures alike
    "FailureMode",
    "SuccessMode",
    "Attribution",
    "classify_failure",
    "classify_success",
    "looks_truncated",
    # statement checking
    "StatementTask",
    "StatementChecker",
    "StatementVerdict",
    "StatementStatus",
    "Check",
    "CheckKind",
    "ProbeKind",
    "load_triplets",
    # judges
    "Judge",
    "JudgeError",
    "MockJudge",
    "ConsensusJudge",
    "JudgeLabel",
    "JudgeVerdict",
    "JudgeUsage",
    "create_judge",
    "register_judge",
    "available_judges",
    # end-to-end
    "EndToEndRunner",
    "EndToEndResult",
    "EndToEndStatus",
]
