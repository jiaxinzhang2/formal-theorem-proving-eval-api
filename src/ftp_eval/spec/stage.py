"""Names and statuses shared by grading results."""
from enum import Enum

__all__ = ["StageId", "StageStatus"]

class StageId(str, Enum):
    INTERFACE = "interface"
    KERNEL = "kernel"
    AXIOMS = "axioms"
    REPORT = "report"

    @property
    def number(self) -> int:
        return {"interface": 1, "kernel": 2, "axioms": 3, "report": 4}[self.value]

class StageStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"

    @property
    def conclusive(self) -> bool:
        return self is not StageStatus.NOT_RUN
