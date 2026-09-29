# `ftp_eval`

Read [`spec/`](spec/) first. It is pure interface — abstract base classes
and the shapes they exchange, no logic — so it is the short version of the
whole design.

One input format: **Lean files**. A problem is one `.lean` file with one
theorem, an answer is another, a benchmark is a folder of problems.

## Two APIs

```
proving/            does this answer prove this theorem?
autoformalization/  is this statement faithful to the prose?
```

Separate, because grading a contest never asks the second question and
auditing a problem set never asks the first.

## Where a module goes

```
above both APIs -- these import them
  cli.py            the six commands
  registry.py       name -> backend or judge
  io.py             read and write JSONL
  __init__.py       the public surface

below both APIs -- these know about neither
  shared/types.py       the backend contract
  shared/comments.py    what counts as a comment, per language
  shared/soundness.py   screening source text for ways to game a checker

inside one API -- only that side uses it
  proving/analysis/       metrics over proofs, and the statistics over them
  autoformalization/types.py       statement verdicts, judge records
  autoformalization/complexity.py  metrics over statements
```

`shared/` is three files, not a junk drawer. If a module in it ever needs
to import from one of the two APIs, it does not belong there.

`soundness.py` is the one module the two APIs use *differently*, so
`screen_source` says so in its signature: `PROOF_HACK_CLASSES` for a
submitted answer, `STATEMENT_HACK_CLASSES` for a problem statement. That
distinction is not cosmetic — a problem file's `sorry` is the hole a
participant fills, so screening a statement with the proof-side rules
reports every well-formed problem as malformed.

The whole public API is importable straight from `ftp_eval`; these paths
matter only when extending the package. Full map in
[../../ARCHITECTURE.md](../../ARCHITECTURE.md).
