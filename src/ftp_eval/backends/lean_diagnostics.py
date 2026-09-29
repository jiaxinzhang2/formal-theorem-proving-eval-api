"""Parse Lean diagnostics and exact, unique axiom-dependency listings."""
from __future__ import annotations

import re
from .types import Diagnostic, ErrorKind, Severity


#: ``file.lean:12:4: error: message`` -- Lean's standard message prefix.
_MESSAGE_RE = re.compile(
    r"^(?P<file>.+):(?P<line>\d+):(?P<col>\d+):\s*(?P<severity>error|warning|info)(?:\([^)]*\))?:\s*(?P<msg>.*)$"
)


#: Ordered, first match wins. Ordering matters: "unknown identifier" is
#: also a type error, and the more specific label is the useful one.
_CLASSIFIERS: tuple[tuple[re.Pattern[str], ErrorKind], ...] = (
    (re.compile(r"unknown (identifier|constant|namespace|tactic)", re.I), ErrorKind.UNKNOWN_IDENTIFIER),
    (re.compile(r"declaration uses 'sorry'", re.I), ErrorKind.INCOMPLETE),
    (re.compile(r"unsolved goals", re.I), ErrorKind.UNSOLVED_GOALS),
    # Lean writes this as "(deterministic) timeout at whnf", so the
    # parenthesis has to be optional or the match silently misses.
    (re.compile(r"(maximum recursion depth|\(?deterministic\)?\s+timeout|maxHeartbeats|out of memory)", re.I), ErrorKind.RESOURCE_LIMIT),
    (re.compile(r"(unexpected token|expected .*(token|term|command)|unexpected end of input)", re.I), ErrorKind.SYNTAX),
    (re.compile(r"(linarith failed|nlinarith failed|simp made no progress|ring_nf failed|tactic .* failed|omega could not)", re.I), ErrorKind.TACTIC_FAILED),
    (re.compile(r"(type mismatch|application type mismatch|failed to synthesize|function expected)", re.I), ErrorKind.TYPE),
)


def classify_lean_message(message: str) -> ErrorKind:
    """Map one Lean error message onto the shared taxonomy."""
    for pattern, kind in _CLASSIFIERS:
        if pattern.search(message):
            return kind
    return ErrorKind.UNKNOWN


def parse_lean_log(text: str) -> list[Diagnostic]:
    """Parse ``lean``'s stdout/stderr into diagnostics.

    Continuation lines (the goal state under an "unsolved goals" error)
    are attached to the diagnostic they belong to, because the first line
    alone rarely says what actually went wrong.
    """
    diagnostics: list[Diagnostic] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending or not diagnostics:
            pending.clear()
            return
        last = diagnostics[-1]
        extra = "\n".join(pending).rstrip()
        pending.clear()
        if extra:
            diagnostics[-1] = Diagnostic(
                severity=last.severity,
                message=(last.message + "\n" + extra).strip(),
                line=last.line,
                column=last.column,
                kind=last.kind,
            )

    for raw_line in text.splitlines():
        match = _MESSAGE_RE.match(raw_line.strip())
        if match:
            flush()
            message = match.group("msg").strip()
            severity = Severity(match.group("severity"))
            diagnostics.append(
                Diagnostic(
                    severity=severity,
                    message=message,
                    line=int(match.group("line")),
                    column=int(match.group("col")),
                    kind=classify_lean_message(message) if severity is Severity.ERROR else None,
                )
            )
        elif diagnostics:
            pending.append(raw_line)
    flush()

    # Re-classify now that continuation lines are attached: "unsolved
    # goals" often only appears in the body.
    out: list[Diagnostic] = []
    for d in diagnostics:
        if d.severity is Severity.ERROR and d.kind is ErrorKind.UNKNOWN:
            d = Diagnostic(d.severity, d.message, d.line, d.column, classify_lean_message(d.message))
        out.append(d)
    return out


#: ``'foo' depends on axioms: [propext, Classical.choice]`` -- or
#: ``'foo' does not depend on any axioms``.
_AXIOMS_LINE_RE = re.compile(
    r"^[ \t]*'(?P<name>[^']+)'\s+(?:depends on axioms:\s*\[(?P<axioms>[^\]]*)\]"
    r"|does not depend on any axioms)[ \t]*$",
    re.MULTILINE,
)


def parse_printed_axioms(log: str, name: str) -> list[str] | None:
    """Read a ``#print axioms`` listing out of Lean's output.

    Returns the axiom names, ``[]`` when Lean said the declaration depends
    on none, or ``None`` when no listing for ``name`` was found -- which
    means the audit did not run and must not be read as a clean result.
    """
    matches = [match for match in _AXIOMS_LINE_RE.finditer(log) if match.group("name") == name]
    if len(matches) != 1:
        return None
    raw = matches[0].group("axioms")
    return [] if raw is None else [a.strip() for a in raw.split(",") if a.strip()]
