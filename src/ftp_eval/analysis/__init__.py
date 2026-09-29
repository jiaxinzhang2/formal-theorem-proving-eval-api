"""What happened, for passes and failures alike.

None of this decides whether an attempt succeeded -- that is the two link
layers' job. This is what you read afterwards to understand *why*, and it
runs on every attempt regardless of outcome, because how a model fails is
as informative as how it succeeds and most of these are only interesting
as the comparison between the two.

* :mod:`~ftp_eval.analysis.tactics` -- which tactics, in what order, with
  what opening, closing and transition patterns.
* :mod:`~ftp_eval.analysis.structure` -- proof shape (declarations, named
  steps, dependency depth, nesting, comments, repetition), statement
  complexity, distributions, and the metric/outcome correlation search.
* :mod:`~ftp_eval.analysis.modes` -- fine-grained failure modes with an
  attribution (model vs budget vs harness), and success modes describing
  what kind of proof passed.
* :mod:`~ftp_eval.analysis.scoring` -- unbiased pass@k and the ``Summary``
  that ties all of it together.

Measured cost of the whole layer: ~0.18 ms per attempt. See
``tests/measure_speed.py`` and ``tests/test_performance.py``.
"""

from __future__ import annotations

__all__ = ["tactics", "structure", "modes", "scoring"]
