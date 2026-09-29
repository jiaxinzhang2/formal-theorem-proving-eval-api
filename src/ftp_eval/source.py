"""Source-text helpers shared by every layer.

Small on purpose. It exists because comment stripping is needed by the
reward-hacking screen, by tactic extraction and by the structural metrics,
and having the analysis modules import it from the soundness module was a
misleading dependency -- it suggested analysis depended on the screen,
when both merely need to ignore comments.

Comment handling matters in both directions, which is why it is one
function rather than three near-copies: ``-- TODO: remove the sorry`` must
not fail an honest proof, and a ``sorry`` hidden inside ``/- ... -/`` must
not pass a dishonest one.
"""

from __future__ import annotations

import re

__all__ = ["strip_comments", "COMMENT_SYNTAX", "TOKEN_RE", "IDENTIFIER_RE"]

#: language -> ((regex, replacement), ...) applied in order, block
#: comments before line comments.
COMMENT_SYNTAX: dict[str, tuple[tuple[str, str], ...]] = {
    "lean4": ((r"/-(?:.|\n)*?-/", " "), (r"--[^\n]*", " ")),
    "lean3": ((r"/-(?:.|\n)*?-/", " "), (r"--[^\n]*", " ")),
    "coq": ((r"\(\*(?:.|\n)*?\*\)", " "),),
    "isabelle": ((r"\(\*(?:.|\n)*?\*\)", " "),),
}

_COMPILED: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    language: tuple((re.compile(pattern), replacement) for pattern, replacement in rules)
    for language, rules in COMMENT_SYNTAX.items()
}

#: Every token: identifiers, numbers, and single punctuation characters.
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'!?.]*|\d+|[^\sA-Za-z0-9_]")

#: Bare identifiers, excluding the tail of a qualified name.
IDENTIFIER_RE = re.compile(r"(?<![.\w'])[A-Za-z_][A-Za-z0-9_'!?]*")


def strip_comments(text: str, language: str) -> str:
    """Remove comments, so no check can be commented past in either direction.

    Returns ``text`` unchanged for a language with no known comment
    syntax, rather than guessing at one.
    """
    out = text
    for pattern, replacement in _COMPILED.get(language, ()):
        out = pattern.sub(replacement, out)
    return out
