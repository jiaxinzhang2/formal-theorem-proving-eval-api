"""Splitting a Lean 4 file into declarations.

Needed because the real unit of submission is a *file*, not a statement.
A formal-conjectures file holds several theorems plus the ``abbrev``
definitions they share, and a participant submits a whole file back. To
judge a submission you have to find the target declaration in both files,
which means knowing where each declaration starts and ends.

Deliberately lexical, and deliberately conservative: a declaration this
cannot parse is reported as unparsed rather than guessed at, because a
mis-sliced declaration would be compared against the wrong thing and
produce a confident wrong verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..backends.comments import strip_comments

__all__ = [
    "LeanDeclaration",
    "LeanFile",
    "parse_lean_file",
    "ANSWER_PLACEHOLDER",
    "normalize_signature",
]

#: What an ``answer(...)`` hole is replaced with before two signatures are
#: compared. A submission is *supposed* to change these, so comparing them
#: literally would flag every solved problem.
ANSWER_PLACEHOLDER = "␀ANSWER␀"

#: Declaration keywords that introduce a named top-level item.
_DECL_KEYWORDS = (
    "theorem", "lemma", "example", "abbrev", "def", "instance", "structure",
    "inductive", "class", "axiom", "opaque", "constant", "proof_wanted",
)

_MODIFIERS = (
    "private", "protected", "public", "noncomputable", "unsafe", "partial",
    "nonrec", "local", "scoped", "@[expose]",
)

#: A declaration header at the start of a line, after optional modifiers.
_DECL_START_RE = re.compile(
    r"^(?P<modifiers>(?:(?:%s)\s+)*)(?P<kind>%s)\s+(?P<rest>.*)$"
    % ("|".join(re.escape(m) for m in _MODIFIERS), "|".join(_DECL_KEYWORDS)),
    re.MULTILINE,
)

_ATTRIBUTE_BLOCK_RE = re.compile(r"@\[(?P<body>(?:[^\[\]]|\[[^\]]*\])*)\]", re.DOTALL)
_DOCSTRING_RE = re.compile(r"/--(?P<body>(?:.|\n)*?)-/", re.DOTALL)
_ANSWER_RE = re.compile(r"\banswer\s*\(")

_OPENERS = "([{⟨⦃"
_CLOSERS = ")]}⟩⦄"


@dataclass(frozen=True)
class LeanDeclaration:
    """One top-level declaration, taken apart."""

    kind: str
    name: str
    #: Raw attribute bodies, e.g. ``("category research open", "AMS 5 11")``.
    attributes: tuple[str, ...] = ()
    #: Everything between the name and the top-level ``:=`` (or ``where``).
    signature: str = ""
    #: Everything after it. For a conjecture this is just ``sorry``.
    body: str = ""
    docstring: str = ""
    #: 1-based line where the declaration (or its attributes) begins.
    start_line: int = 0
    #: The declaration's full source text, attributes and docstring included.
    source: str = ""
    #: Enclosing ``namespace``/``section`` path at the point of declaration.
    #: Part of the declaration's identity: the same bare name under a
    #: different namespace is a different theorem.
    namespace: str = ""

    @property
    def qualified_name(self) -> str:
        """``Demo.t`` for ``theorem t`` inside ``namespace Demo``."""
        if not self.namespace:
            return self.name
        # A name already written with the namespace prefix is not doubled.
        if self.name == self.namespace or self.name.startswith(self.namespace + "."):
            return self.name
        return "%s.%s" % (self.namespace, self.name)

    @property
    def categories(self) -> tuple[str, ...]:
        """Values of the ``@[category ...]`` attribute, if present."""
        for attribute in self.attributes:
            match = re.match(r"\s*category\s+(?P<values>[^,\]]+)", attribute)
            if match:
                return tuple(match.group("values").split())
        return ()

    @property
    def ams_tags(self) -> tuple[str, ...]:
        for attribute in self.attributes:
            match = re.search(r"\bAMS\s+(?P<values>[\d\s]+)", attribute)
            if match:
                return tuple(match.group("values").split())
        return ()

    @property
    def answer_holes(self) -> int:
        """How many ``answer(...)`` holes the signature has."""
        return len(_ANSWER_RE.findall(self.signature))

    @property
    def is_open_hole(self) -> bool:
        """Whether an ``answer(...)`` hole is still unfilled (``answer(sorry)``)."""
        return any(
            arg.strip() == "sorry" for arg in extract_answer_arguments(self.signature)
        )

    @property
    def is_unproved(self) -> bool:
        """Whether the body is only a placeholder.

        In formal-conjectures a ``sorry`` body is the *normal* state of an
        open conjecture, so this is a description, not a violation.
        """
        stripped = strip_comments(self.body, "lean4").strip()
        return stripped in ("sorry", "by sorry", ":= sorry") or re.fullmatch(
            r"(?:by\s+)?sorry\s*", stripped
        ) is not None

    @property
    def is_statement(self) -> bool:
        return self.kind in ("theorem", "lemma", "example", "proof_wanted")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "attributes": list(self.attributes),
            "categories": list(self.categories),
            "ams": list(self.ams_tags),
            "signature": self.signature,
            "answer_holes": self.answer_holes,
            "is_open_hole": self.is_open_hole,
            "is_unproved": self.is_unproved,
            "start_line": self.start_line,
        }


@dataclass
class LeanFile:
    """A parsed Lean 4 source file."""

    #: Everything before the first declaration: module header, imports,
    #: ``open`` lines, ``namespace``. Part of the problem's context.
    preamble: str = ""
    declarations: tuple[LeanDeclaration, ...] = ()
    #: Line ranges the parser could not attribute to a declaration.
    unparsed: tuple[str, ...] = ()
    #: Trailing scope closers (``end Namespace``), which belong to the file
    #: rather than to the last declaration's proof.
    epilogue: str = ""
    source: str = ""

    def by_name(self) -> dict[str, LeanDeclaration]:
        """Fully-qualified name -> declaration.

        Qualified, not bare: ``theorem t`` inside ``namespace Demo`` is
        ``Demo.t``, and the same bare name under a different namespace is a
        *different* theorem. Matching on the bare name would accept an
        answer that proves ``Other.t`` when ``Demo.t`` was asked for.

        Bare names are also registered as aliases when unambiguous, so a
        caller naming a target without its namespace still finds it.
        """
        out: dict[str, LeanDeclaration] = {}
        bare_counts: dict[str, int] = {}
        for declaration in self.declarations:
            out[declaration.qualified_name] = declaration
            bare_counts[declaration.name] = bare_counts.get(declaration.name, 0) + 1
        for declaration in self.declarations:
            if bare_counts[declaration.name] == 1:
                out.setdefault(declaration.name, declaration)
        return out

    def duplicate_names(self) -> tuple[str, ...]:
        """Names declared more than once.

        Lean rejects a duplicate declaration, so a file containing one will
        not compile -- but silently picking one of them would mean judging a
        file that cannot exist, and choosing the wrong one is exactly how a
        decoy would work.
        """
        seen: dict[str, int] = {}
        for declaration in self.declarations:
            key = declaration.qualified_name
            seen[key] = seen.get(key, 0) + 1
        return tuple(sorted(name for name, count in seen.items() if count > 1))

    def statements(self) -> tuple[LeanDeclaration, ...]:
        return tuple(d for d in self.declarations if d.is_statement)

    def definitions(self) -> tuple[LeanDeclaration, ...]:
        return tuple(d for d in self.declarations if not d.is_statement)

    def imports(self) -> tuple[str, ...]:
        return tuple(re.findall(r"^\s*(?:public\s+)?import\s+(\S+)", self.preamble, re.MULTILINE))

    def to_dict(self) -> dict[str, Any]:
        return {
            "imports": list(self.imports()),
            "declarations": [d.to_dict() for d in self.declarations],
            "unparsed": list(self.unparsed),
        }


_NAMESPACE_RE = re.compile(r"^\s*(namespace|end)\s+(\S+)\s*$", re.MULTILINE)


def _namespace_at(source: str, offset: int) -> str:
    """The enclosing ``namespace`` path at a point in the file.

    Walks the ``namespace X`` / ``end X`` pairs before ``offset``. An
    ``end`` that does not match the open namespace (it closes a ``section``)
    is ignored rather than popping the wrong scope.
    """
    stack: list[str] = []
    for match in _NAMESPACE_RE.finditer(source, 0, offset):
        keyword, name = match.group(1), match.group(2)
        if keyword == "namespace":
            stack.append(name)
        elif stack and stack[-1] == name:
            stack.pop()
    return ".".join(stack)


def _block_start(source: str, keyword_offset: int, floor: int) -> int:
    """Where a declaration's block begins, attributes and docstring included.

    Walks back from the keyword over blank lines, ``@[...]`` attribute
    blocks and a ``/-- ... -/`` docstring, stopping at ``floor`` (the
    previous declaration's keyword) so it can never claim someone else's
    text.
    """
    index = keyword_offset
    while index > floor:
        head = source.rfind("\n", floor, index - 1)
        line_start = floor if head == -1 else head + 1
        line = source[line_start:index].strip()
        if not line:
            index = line_start
            continue
        # Attribute blocks and docstrings can span several lines, so jump
        # to the opening delimiter rather than walking line by line. The
        # opening must sit at the start of its own line: that is what keeps
        # a proof line such as `simp [foo]` or a body containing `-/` from
        # dragging the boundary back over the previous declaration.
        claimed = None
        if line.endswith("-/"):
            claimed = _line_anchored(source, "/--", floor, index) or _line_anchored(
                source, "/-", floor, index
            )
        elif line.endswith("]"):
            claimed = _line_anchored(source, "@[", floor, index)
        if claimed is None:
            break
        index = claimed
    return index


def _line_anchored(source: str, opener: str, floor: int, limit: int) -> int | None:
    """Offset of the last ``opener`` before ``limit`` that starts its line."""
    at = source.rfind(opener, floor, limit)
    while at != -1:
        line_start = source.rfind("\n", 0, at) + 1
        if not source[line_start:at].strip():
            return at
        at = source.rfind(opener, floor, at)
    return None


def _find_top_level(text: str, needle: str) -> int:
    """Index of ``needle`` at bracket depth zero, or -1."""
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
        elif depth == 0 and text.startswith(needle, i):
            return i
        i += 1
    return -1


#: File-level commands that close a scope. They sit at column 0 after the
#: last declaration and are not part of its body.
_EPILOGUE_RE = re.compile(r"^(?:end(?:\s+\S+)?|attribute\s|#\w+)\s*$", re.MULTILINE)


def _trim_epilogue(body: str) -> tuple[str, str]:
    """Split a trailing ``end Namespace`` block off a declaration's body.

    Without this, the last declaration in a file absorbs the file's closing
    ``end``, and ``is_unproved`` is then wrong for it -- which with one
    problem per file is wrong for the target every time, since the target
    *is* the last declaration.
    """
    lines = body.splitlines()
    cut = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.strip():
            continue
        # Only a column-0 command closes a file-level scope; an indented
        # `end` belongs to a tactic block.
        if line[:1] not in (" ", "\t") and _EPILOGUE_RE.match(line.strip()):
            cut = index
            continue
        break
    return "\n".join(lines[:cut]).rstrip(), "\n".join(lines[cut:]).strip()


def _split_signature_and_body(rest: str) -> tuple[str, str]:
    """Split a declaration at its top-level ``:=`` or ``where``.

    ``:=`` inside binders, inside ``answer(...)``, or inside a structure
    instance is at non-zero depth and is skipped.
    """
    seam = _find_top_level(rest, ":=")
    if seam == -1:
        # `theorem foo : P := by` written as `theorem foo : P where` or a
        # tactic block introduced by `by` with no `:=` is unusual but real.
        where = _find_top_level(rest, " where")
        if where != -1:
            return rest[:where].rstrip(), rest[where:].strip()
        return rest.rstrip(), ""
    return rest[:seam].rstrip(), rest[seam + 2 :].strip()


def extract_answer_arguments(signature: str) -> tuple[str, ...]:
    """The text inside each ``answer(...)`` in a signature, in order.

    Bracket-matched rather than regex-terminated, so a nested call such as
    ``answer(Finset.card {1, 2})`` comes back whole.
    """
    out: list[str] = []
    for match in _ANSWER_RE.finditer(signature):
        start = match.end()  # just past the "("
        depth = 1
        i = start
        while i < len(signature) and depth:
            if signature[i] in _OPENERS:
                depth += 1
            elif signature[i] in _CLOSERS:
                depth -= 1
            i += 1
        if depth == 0:
            out.append(signature[start : i - 1])
    return tuple(out)


def normalize_signature(signature: str) -> str:
    """Canonical form for comparing two statements of the same theorem.

    Two things are deliberately normalized away, and nothing else:

    * **``answer(...)`` contents.** A submission is supposed to fill these
      in, so comparing them literally would flag every solved problem as
      tampering. What must not change is *where* the holes are, so each
      becomes a fixed placeholder and the count and position still matter.
    * **Whitespace and comments.** Line wrapping is not meaning.

    Everything else -- binders, implicitness, types, the conclusion -- is
    compared exactly. That is the point: a submission that weakens a
    hypothesis or changes a bound has changed the theorem.
    """
    out = strip_comments(signature, "lean4")
    # Replace answer(...) bodies, innermost-safe via the bracket matcher.
    for argument in extract_answer_arguments(out):
        out = out.replace("answer(%s)" % argument, ANSWER_PLACEHOLDER, 1)
        out = out.replace("answer( %s )" % argument, ANSWER_PLACEHOLDER, 1)
    out = _ANSWER_RE.sub(ANSWER_PLACEHOLDER + "(", out)
    out = re.sub(r"\s+", " ", out).strip()
    # A trailing `by` belongs to the proof, not the claim.
    return re.sub(r"\bby\s*$", "", out).strip()


def parse_lean_file(source: str) -> LeanFile:
    """Split a Lean 4 file into its declarations.

    Attributes and the docstring immediately above a declaration are
    attached to it, since ``@[category research open]`` is part of what the
    problem says about itself.
    """
    if not source.strip():
        return LeanFile(source=source)

    starts: list[tuple[int, re.Match[str]]] = []
    for match in _DECL_START_RE.finditer(source):
        # Skip a keyword that is really inside a term, e.g. `fun` bodies
        # indented under a proof: a real declaration starts at column 0.
        if match.start() != 0 and source[match.start() - 1] != "\n":
            continue
        starts.append((match.start(), match))

    if not starts:
        return LeanFile(preamble=source, source=source)

    # A declaration's *block* includes the attributes and docstring above
    # its keyword. Computing these boundaries first matters: slicing a body
    # up to the next keyword instead would swallow the next declaration's
    # docstring, and `is_unproved` would then be wrong for every conjecture
    # that has one -- which in this format is all of them.
    blocks = [_block_start(source, offset, starts[i - 1][0] if i else 0)
              for i, (offset, _) in enumerate(starts)]

    declarations: list[LeanDeclaration] = []
    unparsed: list[str] = []
    epilogues: list[str] = []

    for index, (offset, match) in enumerate(starts):
        end = blocks[index + 1] if index + 1 < len(starts) else len(source)
        preceding = source[blocks[index] : offset]
        attributes = tuple(body.strip() for body in _ATTRIBUTE_BLOCK_RE.findall(preceding))
        docstrings = _DOCSTRING_RE.findall(preceding)

        rest = match.group("rest")
        tail = source[offset + match.end() - match.start() : end]
        full_rest = rest + "\n" + tail if tail else rest

        name_match = re.match(r"([^\s({\[:⦃]+)", rest.strip())
        if not name_match and match.group("kind") != "example":
            unparsed.append(source[offset:end][:200])
            continue
        name = name_match.group(1) if name_match else "example"
        after_name = full_rest[full_rest.find(name) + len(name) :] if name_match else full_rest

        signature, body = _split_signature_and_body(after_name)
        body, epilogue = _trim_epilogue(body)
        if epilogue:
            epilogues.append(epilogue)
        declarations.append(
            LeanDeclaration(
                kind=match.group("kind"),
                name=name,
                attributes=attributes,
                signature=signature.strip(),
                body=body.rstrip(),
                docstring=docstrings[-1].strip() if docstrings else "",
                start_line=source[: blocks[index]].count("\n") + 1,
                source=source[blocks[index] : end].rstrip(),
                namespace=_namespace_at(source, offset),
            )
        )

    return LeanFile(
        preamble=source[: blocks[0]].rstrip(),
        declarations=tuple(declarations),
        unparsed=tuple(unparsed),
        epilogue="\n".join(epilogues).strip(),
        source=source,
    )
