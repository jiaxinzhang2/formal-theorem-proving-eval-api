"""Link 2: does this proof close this goal?

    formal statement  ──▶  formal proof
                      valid?

* :mod:`~ftp_eval.proving.verifier` -- the interface every backend
  implements, plus source assembly, the soundness gate, and the statement
  probes a backend can answer.
* :mod:`~ftp_eval.proving.runner` -- batch execution: streaming results,
  resume, a content-keyed verdict cache, bounded concurrency.
* :mod:`~ftp_eval.proving.backends` -- the provers themselves.

The reward-hacking screen lives one level up, in
:mod:`ftp_eval.soundness`, because the statement layer uses it too.
"""

from __future__ import annotations

__all__ = ["verifier", "runner", "backends"]
