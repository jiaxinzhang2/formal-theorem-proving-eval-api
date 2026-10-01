"""Comment handling for prover source text.

One function, deliberately. All three layers need to ignore comments --
the reward-hacking screen, tactic extraction and the structural metrics --
and before this existed they all imported it from
:mod:`ftp_eval.backends.soundness`, which implied the analysis code depended on the
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

#: Languages handled by the scanner below rather than by regex. Regex
#: cannot express "not inside a string literal" or "nested to depth n",
#: and both of those are exploitable -- see :func:`_scan`.
_SCANNED = {
    "lean4": ("--", "/-", "-/"),
    "lean3": ("--", "/-", "-/"),
}


#: A Lean character literal, which may legitimately contain a quote or a
#: comment delimiter: `'"'`, `'\''`, `'«'`.
_CHAR_LITERAL = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)|[^'\\\n])'")

#: The opening of a raw string literal: `r"`, `r#"`, `r##"`, ...
_RAW_STRING = re.compile(r'r#*"')


def _ident_char(ch: str) -> bool:
    """Whether ``ch`` can precede a prime inside one Lean identifier."""
    return bool(ch) and (ch.isalnum() or ch in "_'!?ₓ₁₂₃₄₅₆₇₈₉₀" or ord(ch) > 0x7F)


def _char_literal(text: str, i: int) -> int:
    """Length of the character literal at ``i``, or 0 if there is none."""
    match = _CHAR_LITERAL.match(text, i)
    return match.end() - match.start() if match else 0


def _scan(text: str, line: str, block_open: str, block_close: str) -> str:
    """Strip comments with a scanner that understands strings and nesting.

    Two things a regex gets wrong here, both of which an adversarial
    submission can use:

    * **String literals.** ``s = "a -- b"`` is not a comment. A regex
      stripper deletes from ``--`` to end of line, so ``"a -- ALPHA"`` and
      ``"a -- OMEGA"`` normalize to the same text and two *different*
      theorems compare equal. That is a false match, the expensive
      direction.
    * **Nested block comments.** Lean's ``/- -/`` nests. A non-greedy regex
      stops at the first ``-/``, so ``/- /- -/ axiom cheat -/`` leaves
      ``axiom cheat -/`` looking like live code. That direction is only
      noise -- an honest file gets flagged -- but it is avoidable.

    Comments become spaces, never nothing, so stripping cannot glue two
    tokens into a third.
    """
    out: list[str] = []
    i, n = 0, len(text)
    depth = 0
    while i < n:
        ch = text[i]
        if depth:
            if text.startswith(block_open, i):
                depth += 1
                out.append("  ")
                i += 2
                continue
            if text.startswith(block_close, i):
                depth -= 1
                out.append("  ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        if ch == "«":
            # A French-quoted identifier holds arbitrary characters, so
            # `def «/-» := 0` is two declarations to Lean and the start of
            # a block comment to a scanner that does not know about them.
            # Everything up to the matching `«-/»` then disappears from the
            # screened text while Lean compiles it: `sorry`, a fresh
            # `axiom`, `native_decide` and `#eval` all go invisible at once.
            # Copy the identifier verbatim instead.
            closed = text.find("»", i + 1)
            end = n if closed < 0 else closed + 1
            out.append(text[i:end])
            i = end
            continue

        if ch == "'" and _char_literal(text, i) and not _ident_char(text[i - 1] if i else ""):
            # `'"'` is one character, not the start of a string. Reading it
            # as a quote desynchronises everything after it, and the next
            # `/-` Lean sees inside a *string* would then swallow live code.
            # A trailing prime (`h'`) is an identifier and must not match,
            # which is what the preceding-character test is for.
            end = i + _char_literal(text, i)
            out.append(text[i:end])
            i = end
            continue

        if ch == "r" and not _ident_char(text[i - 1] if i else ""):
            # A raw string `r#"..."#` ends only at a quote followed by the
            # same number of hashes, and holds unescaped quotes until then.
            # Reading it as an ordinary literal ends the string early and
            # desynchronises every delimiter after it.
            raw = _RAW_STRING.match(text, i)
            if raw is not None:
                closer = '"' + "#" * (raw.end() - i - 2)
                closed = text.find(closer, raw.end())
                end = n if closed < 0 else closed + len(closer)
                out.append(text[i:end])
                i = end
                continue

        if ch == '"':
            # Copy the literal verbatim, escapes included, so nothing
            # inside it is ever read as a comment.
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue

        if text.startswith(block_open, i):
            depth = 1
            out.append("  ")
            i += 2
            continue

        if text.startswith(line, i):
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def strip_comments(text: str, language: str) -> str:
    """Replace comments with whitespace.

    Whitespace rather than nothing, so that stripping cannot glue two
    tokens together: ``simp/- x -/[foo]`` must not become ``simp[foo]``
    with a different meaning.

    Lean is handled by a scanner that respects string literals and nested
    block comments (see :func:`_scan`); other languages use the regex
    rules in :data:`COMMENT_SYNTAX`. Text in a language with no known
    comment syntax comes back unchanged rather than guessed at.
    """
    scanned = _SCANNED.get(language)
    if scanned is not None:
        return _scan(text, *scanned)
    out = text
    for pattern, replacement in _COMPILED.get(language, ()):
        out = pattern.sub(replacement, out)
    return out
