"""Universal-value health probes for the frozen problem format."""
from __future__ import annotations

import os
import pytest

from ftp_eval.backends.lean4 import Lean4Verifier
from ftp_eval.backends.types import ModuleBuild, StatementTask, Status
from ftp_eval.proving.running.problem_health import HealthCheck, HealthKind, ProblemHealth, check_problem_health, format_health_summary


PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"




class HealthBackend:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def build_modules(self, modules, **kwargs):
        self.calls.append(modules)
        return ModuleBuild(Status.VERIFIED if len(modules) == 1 else self.outcome, axioms=())


def test_frozen_health_catches_universal_value_acceptance():
    backend = HealthBackend(Status.VERIFIED)
    health = check_problem_health(backend, StatementTask("P001", "", PROBLEM.replace("n = 1", "True")), module="FtpEvalBench.P001")
    assert health.ungradeable
    assert "∀ ftp_value_0, @Problem.Target ftp_value_0" in backend.calls[-1][-1].source
    assert "UNGRADEABLE" in format_health_summary([health])
    assert health.raw[HealthKind.NON_VACUOUS.value]["build"]["status"] == "verified"


def test_failed_health_search_does_not_prove_non_vacuity():
    health = check_problem_health(HealthBackend(Status.FAILED), StatementTask("P001", "", PROBLEM))
    check = next(c for c in health.checks if c.kind is HealthKind.NON_VACUOUS)
    assert check.passed is None and "does not establish" in check.detail


def test_cheap_gold_verification_is_not_a_problem_defect():
    class GoldBackend(HealthBackend):
        def build_modules(self, modules, **kwargs):
            self.calls.append(modules)
            # Simulate successful verification of the supplied witness,
            # but no proof that all candidate values are accepted.
            verified = len(modules) == 1 or "@Problem.Target (60)" in modules[-1].source
            return ModuleBuild(Status.VERIFIED if verified else Status.FAILED, axioms=())

    backend = GoldBackend(Status.VERIFIED)
    source = PROBLEM.replace("n = 1", "n = 60")
    health = check_problem_health(backend, StatementTask("P001", "", source), gold_arguments=("60",))
    observation = next(c for c in health.checks if c.kind is HealthKind.NON_TRIVIAL)
    assert observation.passed is False and observation.informational
    assert not observation.fatal and observation.to_dict()["informational"]
    assert not health.suspect and not health.ungradeable and not health.failures
    assert "finding a witness was not tested" in observation.detail
    assert "difficulty observations" in format_health_summary([health])
    assert any("∀ ftp_value_0, @Problem.Target ftp_value_0" in modules[-1].source for modules in backend.calls)


def test_easy_proof_task_is_information_not_suspect():
    source = "namespace Problem\nabbrev Target : Prop := True\nend Problem\n"
    health = check_problem_health(HealthBackend(Status.VERIFIED), StatementTask("P001", "", source))
    observation = next(c for c in health.checks if c.kind is HealthKind.NON_TRIVIAL)
    assert observation.passed is False and observation.informational
    assert not health.suspect and not health.ungradeable
    assert "easy proof" in health.format_text()


def test_universal_acceptance_stays_fatal_even_with_gold():
    health = check_problem_health(HealthBackend(Status.VERIFIED),
                                  StatementTask("P001", "", PROBLEM.replace("n = 1", "True")),
                                  gold_arguments=("60",))
    vacuity = next(c for c in health.checks if c.kind is HealthKind.NON_VACUOUS)
    assert vacuity.passed is False and vacuity.fatal and not vacuity.informational
    assert health.ungradeable


def test_informational_metadata_cannot_hide_a_fatal_failure():
    check = HealthCheck(HealthKind.NON_VACUOUS, False, "every value accepted", fatal=True, informational=True)
    health = ProblemHealth("P001", (check,))
    assert health.failures == (check,) and health.ungradeable


def test_open_construction_probes_vacuity_without_claiming_search_difficulty():
    backend = HealthBackend(Status.FAILED)
    health = check_problem_health(backend, StatementTask("P001", "", PROBLEM))
    observation = next(c for c in health.checks if c.kind is HealthKind.NON_TRIVIAL)
    assert observation.passed is None and "searching for a valid value" in observation.detail
    assert len(backend.calls) == 2  # elaboration and the universal vacuity probe
    assert HealthKind.NON_TRIVIAL.value not in health.raw


def test_minimum_witness_health_in_real_lean():
    project = os.environ.get("FTP_EVAL_LEAN_PROJECT")
    if not project:
        pytest.skip("set FTP_EVAL_LEAN_PROJECT to a built Std-capable Lake project")
    verifier = Lean4Verifier(project_dir=project, lake=os.environ.get("FTP_EVAL_LAKE", "lake"))
    # A genuine least natural number: 60 satisfies both residues, and finite
    # search rules out every smaller candidate. Verification is cheap once
    # the candidate is supplied; the probe does not search for that candidate.
    source = ("import Std\nnamespace Problem\nabbrev Target (n : Nat) : Prop :=\n"
              "  (n % 13 = 8 ∧ n % 17 = 9) ∧\n"
              "  ∀ m : Fin n, ¬ (m.val % 13 = 8 ∧ m.val % 17 = 9)\nend Problem\n")
    health = check_problem_health(verifier, StatementTask("PMinimum", "", source), gold_arguments=("60",))
    observation = next(c for c in health.checks if c.kind is HealthKind.NON_TRIVIAL)
    assert observation.passed is False and observation.informational, health.to_dict()
    assert not health.suspect and not health.ungradeable, health.to_dict()
    assert health.raw[HealthKind.NON_TRIVIAL.value]["build"]["status"] == "verified"
    false_gold = check_problem_health(verifier, StatementTask("PMinimum", "", source), gold_arguments=("30",))
    assert false_gold.raw[HealthKind.NON_TRIVIAL.value]["build"]["status"] == "failed"
    vacuous = check_problem_health(verifier, StatementTask("PVacuous", "", PROBLEM.replace("n = 1", "True")), gold_arguments=("60",))
    assert vacuous.ungradeable, vacuous.to_dict()


def test_frozen_health_unavailable_reports_capability_reason():
    class Unsupported:
        def build_modules(self, *args, **kwargs):
            return None
    health = check_problem_health(Unsupported(), StatementTask("P001", "", PROBLEM))
    assert not health.probed
    assert "cannot execute frozen-target module probes" in format_health_summary([health])


@pytest.mark.parametrize("outcome", [None, Status.FAILED])
def test_uncompiled_problem_skips_both_probes_with_the_same_reason(outcome):
    class Uncompiled:
        calls = 0

        def build_modules(self, *args, **kwargs):
            self.calls += 1
            return ModuleBuild(outcome) if outcome is not None else None

    backend = Uncompiled()
    health = check_problem_health(backend, StatementTask("P001", "", PROBLEM))
    skipped = [check for check in health.checks
               if check.kind in (HealthKind.NON_TRIVIAL, HealthKind.NON_VACUOUS)]
    assert backend.calls == 1
    assert len(skipped) == 2 and all(check.passed is None for check in skipped)
    assert skipped[0].detail == skipped[1].detail
    assert skipped[1].detail.count("not checked:") == 1
