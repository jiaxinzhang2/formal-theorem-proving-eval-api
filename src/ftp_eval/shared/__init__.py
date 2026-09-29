"""The three things both APIs actually need.

Everything here sits **below** ``proving/`` and ``autoformalization/`` and
knows about neither. That is the rule: if a module in here ever needs to
import from one of the two APIs, it does not belong in here.

::

    types.py       the backend contract -- what a prover is asked and what
                   it answers. Both APIs drive the same Lean toolchain:
                   proving hands it a statement and a proof, and
                   autoformalization hands it a statement and a probe.
    comments.py    what counts as a comment, per language. Both APIs read
                   Lean text and must not be fooled by text inside a
                   comment or a string literal.
    soundness.py   screening source text for the ways it can game a
                   checker -- but the two APIs ask for different subsets,
                   see below.

Three files, not more. Things that only one side uses live with that
side: proof metrics and the statistics over them in
``proving/analysis/``, statement verdicts and judge records in
``autoformalization/types.py``, statement metrics in
``autoformalization/complexity.py``, JSONL writing in ``../io.py``.
``registry.py`` and ``cli.py`` sit *above* both APIs -- they import them
by name -- so they stayed at the top level too.

Asymmetric sharing, made explicit
---------------------------------
``soundness.py`` is the one module the two APIs use differently, so it
says so in its signature rather than leaving the caller to guess.
``screen_source`` takes ``classes`` and ``subject``:

* a submitted **proof** is screened for ``PROOF_HACK_CLASSES``, which is
  everything;
* a problem **statement** is screened for ``STATEMENT_HACK_CLASSES``,
  which excludes placeholders (a problem file's ``sorry`` is the hole a
  participant fills), section ``variable`` bindings (ordinary Lean in a
  problem file) and the heartbeat cap (about proof search, not meaning).

Screening a statement for everything is not a harmless superset: it
reports every well-formed problem as malformed.
"""

from __future__ import annotations

__all__: list[str] = []
