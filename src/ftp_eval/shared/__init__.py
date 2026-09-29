"""What both APIs stand on.

Everything here sits **below** ``proving/`` and ``autoformalization/`` and
knows about neither. That is the rule for this folder: if a module in here
ever needs to import from one of the two APIs, it does not belong in here.

``registry.py`` and ``cli.py`` sit **above** both APIs instead -- they
import them by name -- which is why they stayed at the top level rather
than moving in here.

Five modules, and who actually uses each::

    types.py       the vocabulary both APIs speak: ProofTask, Status,
                   Diagnostic, StatementVerdict, and the rest       both
    soundness.py   reward-hacking detection over source text        both
    comments.py    what counts as a comment, per language           both
                   (soundness.py strips comments before screening)
    stats.py       distributions and point-biserial correlation     proving
    io.py          read and write JSONL                             CLI

The last two are honest outliers. ``stats.py`` is pure numerics with no
domain concepts at all, and today only proof metrics aggregate through it;
statement metrics (``autoformalization/complexity.py``) expose the same
field-list shape so they can. ``io.py`` is here because both APIs' CLI
commands write their verdicts out the same way.
"""

from __future__ import annotations

__all__: list[str] = []
