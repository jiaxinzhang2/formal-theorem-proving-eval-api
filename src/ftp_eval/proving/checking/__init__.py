"""Frozen-target submission checking and evaluation.

Screening can refuse an answer; acceptance requires the generated Check module,
kernel verification and an allowed dependency closure. Value expressions are
elaborated in a trusted Goal module before the answer is imported.
"""
from .interface import PROBLEM_NAMESPACE, SUBMISSION_NAMESPACE, SOLUTION_NAME, TARGET_NAME, CHECK_THEOREM, InterfaceProblem, InterfaceFault, InterfaceReport, InterfaceVerdict
from .screening import read_interface_problem, check_interface, check_arguments
from .modules import build_check_source, build_goal_module, build_submission_modules
from .policy import ContestPolicy
from .evaluator import grade_interface, evaluate_submission

__all__ = ['PROBLEM_NAMESPACE', 'SUBMISSION_NAMESPACE', 'SOLUTION_NAME', 'TARGET_NAME', 'CHECK_THEOREM', 'InterfaceProblem', 'InterfaceFault', 'InterfaceReport', 'InterfaceVerdict', 'read_interface_problem', 'check_interface', 'check_arguments', 'build_check_source', 'build_goal_module', 'build_submission_modules', 'ContestPolicy', 'grade_interface', 'evaluate_submission']
