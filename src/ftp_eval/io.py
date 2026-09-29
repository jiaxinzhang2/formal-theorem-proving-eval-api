"""Reading and writing JSONL.

The inputs to this package are Lean files, not data files, so this module
is deliberately small: it exists because every command writes its verdicts
out the same way. One object per line, so a long grading run streams to
disk as it goes rather than appearing all at once at the end. A plain JSON
array is also accepted on input.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator

__all__ = [
    "read_jsonl",
    "write_jsonl",
]


def read_jsonl(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    """Yield objects from a .jsonl file, or from a .json array."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError("%s: top-level JSON must be an array of objects" % p)
        for i, obj in enumerate(data):
            if not isinstance(obj, dict):
                raise ValueError("%s: item %d is not an object" % (p, i))
            yield obj
        return
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("%s:%d: invalid JSON (%s)" % (p, lineno, exc.msg)) from exc
        if not isinstance(obj, dict):
            raise ValueError("%s:%d: expected an object" % (p, lineno))
        yield obj


def write_jsonl(path: str | os.PathLike[str], rows: Iterable[Any]) -> int:
    """Write ``rows`` (dicts or objects with ``to_dict``) as JSONL."""
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            payload = row.to_dict() if hasattr(row, "to_dict") else row
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            n += 1
    return n
