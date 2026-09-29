"""Comment handling for prover source text.

One function, deliberately. All three layers need to ignore comments --
the reward-hacking screen, tactic extraction and the structural metrics --
and before this existed they all imported it from
:mod:`ftp_eval.soundness`, which implied the analysis code depended on the
reward-hacking screen when in fact both merely need to skip comments.

Comment handling has to be exactly right in *both* directions, which is
why it is one shared function rather than three near-copies that drift:

* ``-- TODO: remove the sorry`` must not fail an honest proof.
* A ``sorry`` hidden inside ``/- ... -/`` must not pass a dishonest one.

Tokenization deliberately does **not** live here. The three callers that
tokenize want three different things -- identifiers excluding the tail of a
qualified name, identifiers including it, and every token including
punctuation -- so a shared "token regex" would be a false abstraction that
one caller would quietly be wrong about.
"""

from __future__ import annotations

import re

__all__ = ["strip_comments", "COMMENT_SYNTAX"]

#: language -> ((pattern, replacement), ...) applied in order. Block
#: comments must come before line comments: otherwise ``/- -- -/`` loses
#: its closing delimiter to the line-comment rule.
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


def strip_comments(text: str, language: str) -> str:
    """Replace comments with whitespace.

    Whitespace rather than nothing, so that stripping cannot glue two
    tokens together: ``simp/- x -/[foo]`` must not become ``simp[foo]``
    with a different meaning.

    Returns ``text`` unchanged for a language with no known comment
    syntax, rather than guessing at one.
    """
    out = text
    for pattern, replacement in _COMPILED.get(language, ()):
        out = pattern.sub(replacement, out)
    return out
