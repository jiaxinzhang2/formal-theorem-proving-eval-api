"""Problem provenance read from Lean module comments and target attributes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from ..lean_file import parse_lean_file


#: Keys recognized in a problem's metadata block. Unknown keys are kept in
#: ``extra`` rather than dropped -- a setter's own bookkeeping field is not
#: ours to discard.
METADATA_KEYS = (
    "mathdb_id",
    "prose",
    "problem_id",
    "source",
    "source_locator",
    "source_version",
    "difficulty",
    "author",
    "checked",
    "contamination",
    "notes",
)


#: `/-! ... -/` module doc comment, which is where the block lives.
_MODULE_DOC_RE = re.compile(r"/-!(?P<body>(?:.|\n)*?)-/", re.DOTALL)


#: `- key: value` or `key: value`, one per line.
_FIELD_RE = re.compile(r"^\s*[-*]?\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class ProblemMetadata:
    """One problem's provenance."""

    problem_id: str = ""
    #: The corresponding MathDB problem number, when the problem came from
    #: there. Kept as a string: identifiers are not arithmetic.
    mathdb_id: str = ""
    #: The natural-language problem this file formalizes. Optional for
    #: grading -- a proof check never reads it -- but `ftp-eval audit`
    #: cannot judge faithfulness without it.
    prose: str = ""
    source: str = ""
    source_locator: str = ""
    source_version: str = ""
    difficulty: str = ""
    author: str = ""
    checked: str = ""
    contamination: str = ""
    notes: str = ""
    #: From the Lean attributes rather than the comment block.
    categories: tuple[str, ...] = ()
    ams_tags: tuple[str, ...] = ()
    #: The theorem this file sets, fully qualified.
    theorem_name: str = ""
    #: Whether the statement has an ``answer(...)`` hole to fill.
    answer_holes: int = 0
    #: Keys the setter used that this does not recognize. Preserved.
    extra: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_provenance(self) -> bool:
        """Whether the problem records where it came from at all."""
        return bool(self.mathdb_id or self.source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "mathdb_id": self.mathdb_id,
            "source": self.source,
            "source_locator": self.source_locator,
            "source_version": self.source_version,
            "difficulty": self.difficulty,
            "author": self.author,
            "checked": self.checked,
            "contamination": self.contamination,
            "notes": self.notes,
            "categories": list(self.categories),
            "ams_tags": list(self.ams_tags),
            "theorem_name": self.theorem_name,
            "answer_holes": self.answer_holes,
            "extra": dict(self.extra),
        }


def parse_problem_metadata(source: str, *, problem_id: str = "") -> ProblemMetadata:
    """Read a problem file's metadata.

    Fields come from the ``/-! ... -/`` block; ``categories`` and
    ``ams_tags`` come from the Lean attributes, where formal-conjectures
    already puts them. Absent metadata yields empty fields rather than an
    error: a problem without provenance is still a problem, and the setter
    is told which ones are missing it rather than being blocked.
    """
    metadata = ProblemMetadata(problem_id=problem_id)

    blocks = _MODULE_DOC_RE.findall(source)
    fields: dict[str, str] = {}
    for block in blocks:
        for key, value in _FIELD_RE.findall(block):
            fields.setdefault(key.lower(), value.strip())

    extra: dict[str, str] = {}
    for key, value in fields.items():
        if key in METADATA_KEYS:
            # problem_id from the file block does not override the caller's,
            # which comes from the filename and is authoritative.
            if key == "problem_id" and problem_id:
                extra.setdefault("declared_problem_id", value)
                continue
            setattr(metadata, key, value)
        else:
            extra[key] = value
    metadata.extra = extra

    parsed = parse_lean_file(source)
    statements = parsed.statements()
    frozen_target = next((d for d in parsed.declarations if d.qualified_name == "Problem.Target"), None)
    target = frozen_target or (statements[0] if statements else None)
    if target is not None:
        metadata.theorem_name = target.qualified_name
        metadata.categories = target.categories
        metadata.ams_tags = target.ams_tags
        metadata.answer_holes = target.answer_holes
    return metadata
