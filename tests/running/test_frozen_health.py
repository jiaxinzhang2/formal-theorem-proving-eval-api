"""Universal-value health probes for the frozen problem format."""
from __future__ import annotations

import pytest

from ftp_eval.backends.types import ModuleBuild, StatementTask, Status
from ftp_eval.proving.running.problem_health import HealthKind, check_problem_health, format_health_summary


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
