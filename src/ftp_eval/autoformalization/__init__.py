"""Judge-only faithfulness: does a formal statement express its prose?

checker.py coordinates the judge result; judge.py provides the interface,
mock and consensus voting; judges/ contains provider adapters. Prover-based
statement health checks live separately in proving/grading/problem_health.py.
"""
from __future__ import annotations

__all__ = ["checker", "judge", "judges"]
