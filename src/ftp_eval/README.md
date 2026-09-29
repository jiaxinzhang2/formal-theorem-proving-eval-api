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

## Three layers

```
proving/  autoformalization/     the two APIs. autoformalization is a judge
        │            │           and nothing else -- one dataclass from
        │            │           below, no prover, no text analysis.
        ▼            ▼
backends/                        talking to a prover, and screening what it
                                 is handed: the vocabulary, the Verifier
                                 interface, mock / lean4 / axle, the
                                 reward-hacking screen a backend may not
                                 opt out of, and the comment scanner it
                                 needs.
        ▼
spec/                            the contracts: 3 ABCs, each one subclassed
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
| is how you talk to a prover, or how you screen what you hand one | `backends/` |
| is used by only one API | inside that API |

There is no `shared/` and no `source/`. Both existed; both were named for
*who used them* rather than for *what they held*, so both stopped
describing their contents as soon as the callers changed — and one of them
is where the two APIs' imports of each other went to hide.
`tests/test_layering.py` fails if either name comes back.

## Two seams worth knowing about

**A backend reports what the prover said, and nothing more.** It does not
measure the proof — what is worth measuring is the proving API's question,
so `proving/analysis/measure.py` fills a verdict's metrics in. This is not
a style preference: while the metrics lived inside `verify()`, grading —
which calls `probe()` — recorded pass/fail and nothing to analyse.

**`backends/soundness.py` is asked for different things by each caller.**
`screen_source` takes `classes` and `subject`: `PROOF_HACK_CLASSES` for a
submitted answer, `STATEMENT_HACK_CLASSES` for a problem statement. Not
cosmetic — a problem file's `sorry` is the hole a participant fills, so
screening a statement with the proof-side rules reports every well-formed
problem as malformed.

The whole public API is importable straight from `ftp_eval`; these paths
matter only when extending the package. Full map in
[../../ARCHITECTURE.md](../../ARCHITECTURE.md).
