"""Reading formal source text.

The bottom layer: parsing Lean files, knowing what counts as a comment,
screening source for the ways it can game a checker, and measuring a
statement's shape. Imports nothing else in this package, which is what
makes it safe for both APIs to sit on.

    comments.py           what counts as a comment, per language. Scanner
                          based, not regex, so text inside a string literal
                          is not mistaken for code.
    lean_file.py          one Lean file -> declarations, with their
                          signatures, bodies, attributes and answer holes
    soundness.py          screening source for constructs that make a
                          prover's verdict hollow, and the report that
                          comes back. Proofs are screened for all 27
                          patterns; problem statements for the 18 that
                          apply to a statement -- see the module docstring,
                          the asymmetry is load bearing.
    statement_metrics.py  a statement's shape: binders, quantifiers,
                          connectives, conclusion size

Neither API owns this layer. `proving/` parses answers with it,
`autoformalization/` measures statements with it, and `prover/` screens
what it is about to accept.
"""

from __future__ import annotations

__all__: list[str] = []
