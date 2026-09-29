"""A frozen-interface verdict with proof metrics for benchmark reports."""
from dataclasses import dataclass, field
from typing import Any, Mapping

from ...backends.types import Status
from ...spec.stage import StageId, StageStatus
from ..interface import InterfaceVerdict

Stage = StageId
__all__ = ["Stage", "StageStatus", "GradedAnswer"]

@dataclass
class GradedAnswer(InterfaceVerdict):
    tactics: tuple[str, ...] = ()
    structure: Mapping[str, Any] = field(default_factory=dict)
    failure_mode: str = ""
    success_mode: str = ""
    modules: Mapping[str, str] = field(default_factory=dict)

    @property
    def compile_time_s(self) -> float | None:
        return self.build.compile_time_s if self.build else None

    @property
    def compile_status(self) -> StageStatus:
        if self.build is None or self.build.status in (Status.ERROR, Status.SKIPPED):
            return StageStatus.NOT_RUN
        return StageStatus.PASSED if self.build.verified else StageStatus.FAILED

    @property
    def failed_at(self) -> Stage | None:
        if self.compile_status is StageStatus.NOT_RUN and self.refused_at == "kernel":
            return None
        return Stage(self.refused_at) if self.refused_at else None

    @property
    def stage_reached(self) -> Stage:
        if self.axiom_audit is not None:
            return Stage.AXIOMS
        if self.build is not None:
            return Stage.KERNEL
        return Stage.INTERFACE

    @property
    def reason(self) -> str:
        if self.report is not None and self.report.faults:
            return self.report.faults[0][1]
        if self.build is None:
            return "kernel check not run; no supported module-build backend"
        if not self.build.verified:
            return next((d.message for d in self.build.diagnostics if d.message), self.build.status.value)
        if self.axiom_audit is None:
            return "axiom audit not run"
        if not self.axiom_audit.ok:
            return "; ".join(self.axiom_audit.violations)
        return "kernel checked the frozen target; axiom audit passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "stage_reached": self.stage_reached.value,
            "failed_at": self.failed_at.value if self.failed_at else None,
            "reason": self.reason,
            "metrics": {
                "tactics": list(self.tactics),
                "failure_mode": self.failure_mode or None,
                "success_mode": self.success_mode or None,
                "structure": dict(self.structure),
            },
        }
