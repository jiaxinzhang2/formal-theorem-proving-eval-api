# `ftp_eval`

Read [`spec/`](spec/) first. It is pure interface — abstract base classes
and the shapes they exchange, no logic — so it is the short version of the
whole design.

One input format: **Lean files**. A problem is one `.lean` file with one
theorem, an answer is another, a benchmark is a folder of problems.

## Two APIs, and they do not touch

```
proving/            does this answer prove this theorem?
autoformalization/  is this statement faithful to the prose?
```

Neither imports the other. [`../../tests/test_layering.py`](../../tests/test_layering.py)
enforces that, because the rule was broken twice while nobody was checking.

## Four layers

```
proving/  autoformalization/     the two APIs
        │                        ── imports go downward only ──
        ▼
backends/                        talking to a prover: the vocabulary, the
                                 Verifier interface, mock / lean4 / axle
        ▼
source/                          reading formal source text: parsing Lean
                                 files, comments, reward-hacking screening,
                                 statement metrics. Imports nothing else.
spec/                            the contracts. No logic.
```

Above all of it, and importing it by name: `cli.py`, `registry.py`,
`io.py`, `__init__.py`.

The layers below the APIs exist because otherwise each API reaches into the
other for machinery that belongs to neither — which is exactly what
happened. `proving/` needed statement metrics and `autoformalization/`
needed the prover interface, so the "two separate APIs" were a circle.

## Where a module goes

| if it | put it in |
|---|---|
| imports the APIs | the top level |
| is how you talk to a prover | `backends/` |
| reads or measures source text, importing nothing | `source/` |
| is used by only one API | inside that API |

There is no `shared/`. A folder named for a relationship rather than for
what it holds is where cross-API imports go to hide.

## Two seams worth knowing about

**A backend reports what the prover said, and nothing more.** It does not
measure the proof — what is worth measuring is the proving API's question,
so `proving/analysis/measure.py` fills a verdict's metrics in. This is not
a style preference: while the metrics lived inside `verify()`, grading —
which calls `probe()` — recorded pass/fail and nothing to analyse.

**`source/soundness.py` is asked for different things by each caller.**
`screen_source` takes `classes` and `subject`: `PROOF_HACK_CLASSES` for a
submitted answer, `STATEMENT_HACK_CLASSES` for a problem statement. Not
cosmetic — a problem file's `sorry` is the hole a participant fills, so
screening a statement with the proof-side rules reports every well-formed
problem as malformed.

The whole public API is importable straight from `ftp_eval`; these paths
matter only when extending the package. Full map in
[../../ARCHITECTURE.md](../../ARCHITECTURE.md).
