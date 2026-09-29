"""LLM-backed faithfulness judges.

Imported lazily through :mod:`ftp_eval.registry`, so a missing SDK in one
judge cannot break the rest of the package. The interface and the
provider-free judges (``MockJudge``, ``ConsensusJudge``) live in
:mod:`ftp_eval.judge`.
"""

from __future__ import annotations

__all__ = ["claude"]
