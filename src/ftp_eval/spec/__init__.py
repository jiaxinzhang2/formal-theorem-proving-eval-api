"""Benchmark and artifact contracts, plus the grading vocabulary.

Benchmark is implemented by ProblemSet; ArtifactWriter by RunDirectory.
Per-answer acceptance belongs to proving.checking.evaluator; proving.running
records benchmark runs and aggregates the returned verdicts.
"""
from .artifacts import ArtifactWriter
from .benchmark import Benchmark, BenchmarkProblem
from .stage import StageId, StageStatus

__all__ = ["Benchmark", "BenchmarkProblem", "ArtifactWriter", "StageId", "StageStatus"]
