"""Benchmark and artifact contracts, plus the grading vocabulary.

Benchmark is implemented by ProblemSet; ArtifactWriter by RunDirectory.
Per-answer grading is implemented by proving.interface.grade_interface:
interface -> kernel -> axiom audit. Reporting aggregates all answers.
"""
from .artifacts import ArtifactWriter
from .benchmark import Benchmark, BenchmarkProblem
from .stage import StageId, StageStatus

__all__ = ["Benchmark", "BenchmarkProblem", "ArtifactWriter", "StageId", "StageStatus"]
