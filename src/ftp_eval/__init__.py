"""Frozen-target theorem proving, statement faithfulness, and proof analysis."""

from __future__ import annotations

__version__ = "0.1.0"

from .autoformalization.checker import StatementChecker
from .proving.analysis.statement_metrics import StatementComplexity, analyze_statement
from .autoformalization.judge import ConsensusJudge, Judge, JudgeError, MockJudge
from .backends.comments import strip_comments
from .jsonl import read_jsonl, write_jsonl
from .proving.analysis.classification import Attribution, FailureMode, SuccessMode, classify_failure, classify_success, looks_truncated
from .proving.analysis.proof_structure import ProofStructure, analyze_proof
from .proving.analysis.tactics import extract_tactics
from .proving.running import (
    ContestResult,
    GradedAnswer,
    ProblemSet,
    Stage,
    StageStatus,
    Submission,
    grade_contest,
    evaluate_benchmark,
    load_problem_set,
    load_submissions,
)
from .proving.lean_file import LeanDeclaration, LeanFile, parse_lean_file
from .proving.checking.policy import ContestPolicy
from .proving.checking.interface import InterfaceFault, InterfaceProblem, InterfaceReport, InterfaceVerdict
from .proving.checking.evaluator import grade_interface, evaluate_submission
from .proving.checking.screening import read_interface_problem, check_interface
from .proving.checking.modules import build_check_source
from .backends.types import ModuleBuild, ModuleSource
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
from .backends.soundness import HackClass, audit_axioms, screen_source
from .proving.analysis.statistics import Correlation, correlate_with_success, distribution, point_biserial
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
    "ContestPolicy", "InterfaceFault", "InterfaceProblem", "InterfaceReport",
    "InterfaceVerdict", "grade_interface", "evaluate_submission", "read_interface_problem", "check_interface",
    "build_check_source", "ModuleBuild", "ModuleSource",
    # -- proving: does this answer prove this theorem? ------------------
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
    "evaluate_benchmark",
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
