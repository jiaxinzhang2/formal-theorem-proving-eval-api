"""Link 1: does this goal say what the problem said?

    natural language  ──▶  formal statement
                      faithful?

The link a proof checker cannot see. A wrong formalization with a valid
proof is a false positive that no amount of proof checking will catch, and
it is the expensive failure of the two.

* :mod:`~ftp_eval.formalizing.checker` -- combines what the prover can
  decide (elaborates, non-trivial, non-vacuous, matches a reference) with
  what only a judge can assess (means the same thing as the prose), and
  keeps the two kinds of evidence separate in the result.
* :mod:`~ftp_eval.formalizing.judge` -- the judge interface, the
  deterministic mock, and consensus voting with rejection re-checks.
* :mod:`~ftp_eval.formalizing.judges` -- provider-backed judges.
"""

from __future__ import annotations

__all__ = ["checker", "judge", "judges"]
