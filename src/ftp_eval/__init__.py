"""ftp_eval -- evaluating formal theorem proving, from Lean files.

One input format: **Lean files**. A problem is one ``.lean`` file with one
theorem; an answer is another. A benchmark is a folder of problems. There is
no second input shape to learn.

Two APIs, because they answer different questions::

    natural language  ──▶  formal statement  ──▶  formal proof
                    autoformalization/       proving/
                    is it faithful?          does it prove it?

    proving/            does this answer prove this theorem?
      lean_file.py        parsing Lean files into declarations
      matching.py         stage 1: statement identity
      verifier.py         stage 2: the backend interface
      backends/           mock, lean4, axle
      analysis/           proof-side metrics: tactics, structure, modes
      grading/            the contest pipeline: N problems, many participants

    autoformalization/  is this formalization faithful to the problem?
      checker.py          prover probes + an LLM judge
      complexity.py       statement-side metrics
      judge.py, judges/   the judge interface and its providers

Shared by both, which is why they sit at the top level::

    spec/          the contracts -- read this first, it is pure interface
    types.py       the vocabulary both APIs speak
    soundness.py   reward-hacking detection
    comments.py    what counts as a comment, per language
    stats.py       distributions and correlation
    registry.py    backend and judge lookup by name

Everything public is importable straight from ``ftp_eval``; the module
paths matter only when extending the package. Full map in
``ARCHITECTURE.md``.

    from ftp_eval import match_submission

    report = match_submission(open("theorem.lean").read(),
                              open("answer.lean").read())
    print(report.verdict)
"""

from __future__ import annotations

__version__ = "0.1.0"

from .autoformalization.checker import StatementChecker
from .proving.analysis.statement_metrics import StatementComplexity, analyze_statement
from .autoformalization.judge import ConsensusJudge, Judge, JudgeError, MockJudge
from .source.comments import strip_comments
from .io import read_jsonl, write_jsonl
from .proving.analysis.modes import (
    Attribution,
    FailureMode,
    SuccessMode,
    classify_failure,
    classify_success,
    looks_truncated,
)
from .proving.analysis.structure import ProofStructure, analyze_proof
from .proving.analysis.tactics import extract_tactics
from .proving.grading import (
    ContestResult,
    GradedAnswer,
    ProblemSet,
    Stage,
    StageStatus,
    Submission,
    grade_contest,
    load_problem_set,
    load_submissions,
)
from .source.lean_file import LeanDeclaration, LeanFile, parse_lean_file
from .proving.matching import (
    MatchReport,
    MatchStatus,
    MismatchKind,
    SubmissionMatcher,
    match_submission,
)
from .backends.verifier import (
    BackendUnavailable,
    RawVerdict,
    Verifier,
    VerifierError,
    assemble_source,
    screen_soundness,
)
from .registry import (
    available,
    available_judges,
    create,
    create_judge,
    register,
    register_judge,
)
from .source.soundness import HackClass, audit_axioms, screen_source
from .proving.analysis.stats import Correlation, correlate_with_success, distribution, point_biserial
from .backends.types import (
    Assembly,
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProbeKind,
    ProofAttempt,
    ProofTask,
    Severity,
    SoundnessReport,
    StatementTask,
    Status,
    VerificationResult,
)

from .autoformalization.types import Check, CheckKind, JudgeLabel, JudgeUsage, JudgeVerdict, StatementStatus, StatementVerdict

__all__ = [
    "__version__",
    # -- proving: does this answer prove this theorem? ------------------
    "match_submission",
    "MatchReport",
    "MatchStatus",
    "MismatchKind",
    "SubmissionMatcher",
    "parse_lean_file",
    "LeanFile",
    "LeanDeclaration",
    "Verifier",
    "RawVerdict",
    "VerifierError",
    "BackendUnavailable",
    "assemble_source",
    "screen_soundness",
    # -- grading a benchmark -------------------------------------------
    "grade_contest",
    "load_problem_set",
    "load_submissions",
    "ProblemSet",
    "Submission",
    "ContestResult",
    "GradedAnswer",
    "Stage",
    "StageStatus",
    # -- autoformalization: is this formalization faithful? ------------
    "StatementChecker",
    "StatementTask",
    "StatementVerdict",
    "StatementStatus",
    "StatementComplexity",
    "analyze_statement",
    "Check",
    "CheckKind",
    "ProbeKind",
    "Judge",
    "JudgeError",
    "MockJudge",
    "ConsensusJudge",
    "JudgeLabel",
    "JudgeVerdict",
    "JudgeUsage",
    # -- reward-hacking detection --------------------------------------
    "HackClass",
    "screen_source",
    "audit_axioms",
    "strip_comments",
    # -- analysis ------------------------------------------------------
    "extract_tactics",
    "analyze_proof",
    "ProofStructure",
    "FailureMode",
    "SuccessMode",
    "Attribution",
    "classify_failure",
    "classify_success",
    "looks_truncated",
    "Correlation",
    "correlate_with_success",
    "distribution",
    "point_biserial",
    # -- shared --------------------------------------------------------
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
    "create",
    "register",
    "available",
    "create_judge",
    "register_judge",
    "available_judges",
    "read_jsonl",
    "write_jsonl",
]
