# Architecture

## One input format

Lean files. That is the whole input contract:

| thing | is | example |
|---|---|---|
| a **problem** | one `.lean` file, one theorem | `problems/P001.lean` |
| an **answer** | one `.lean` file | `submissions/alice/P001.lean` |
| a **benchmark** | a folder of problems | `benchmarks/demo-2026/` |
| a **submission** | a folder per participant | `submissions/alice/` |

No task JSONL, no attempt records, no separate statement file.

## Two APIs, completely separate

```
natural language  ──▶  formal statement  ──▶  formal proof
                  └────────┬───────────┘  └────────┬──────┘
                   autoformalization/         proving/
                   is it faithful?          does it prove it?
```

Separate on purpose, and separate in fact: **neither imports the other**,
and a test enforces it. Grading a contest never asks whether the statement
is faithful, and auditing a problem set never asks whether anybody proved
it. Fusing them into one verdict is how a suspicious statement with a
valid proof gets counted as solved.

| | `proving/` | `autoformalization/` |
|---|---|---|
| asks | does this answer prove this theorem? | does this statement mean what the prose says? |
| input | problem `.lean` + answer `.lean` | problem `.lean` + its `prose:` metadata |
| run when | answers come in | **before** publishing the problems |
| command | `ftp-eval match`, `ftp-eval grade` | `ftp-eval audit` |
| needs | a prover | a prover *and* an LLM judge |

## Four layers, imports strictly downward

```
   proving/                       autoformalization/
   does this answer prove it?     is it faithful?
        │                              │
        │                              │   one dataclass, and that is all
        └──────────────┬───────────────┘
                       ▼
                  backends/       talking to a prover, and screening what it
                  types           is handed: the vocabulary, the Verifier
                  verifier        interface, mock / lean4 / axle, the
                  mock lean4      reward-hacking screen a backend may not
                  axle            opt out of, and the comment scanner that
                  soundness       screen needs
                  comments
                       │
                       ▼
                  spec/           the contracts (3 ABCs)
```

Every arrow points down and there are no others; `tests/test_layering.py`
fails on any that does not.

**`autoformalization/` is the thin one on purpose.** It is a judge and
nothing else, so the only thing it takes from below is the `StatementTask`
dataclass. It has no prover and no text analysis, because every question a
prover or a regex can answer is a question about the Lean rather than about
the problem — and those all live in `proving/grading/problem_health.py`.

There used to be a fourth layer, `source/`, holding the comment scanner and
the soundness screen. It was justified by both APIs needing it — and that
stopped being true once faithfulness became pure judge, so it folded into
`backends/`, where `verifier.py` applies that screen on every accepted proof
anyway. A folder defined by *who uses it* rather than by *what it is* does
not survive its callers changing. That was `shared/`'s problem too, and
`tests/test_layering.py` now fails if either name reappears.

```
src/ftp_eval/
│
│  ── above everything: these import the layers below by name ──
├── __init__.py            the public surface; everything importable from here
├── cli.py                 the six commands
├── registry.py            name -> backend or judge ("lean4", "claude")
├── io.py                  read and write JSONL
│
├── spec/                  THE CONTRACTS. ABCs the real classes subclass,
│   │                      so a drift breaks the build rather than a docstring.
│   ├── benchmark.py         what a benchmark provides. ProblemSet implements it.
│   ├── stage.py             what a pipeline stage provides
│   └── artifacts.py         what a results directory contains. RunDirectory
│                            implements it.
│
├── backends/              TALKING TO A PROVER, and screening what it is
│                          handed. Imports spec/ only.
│   ├── types.py             what a prover is asked (ProofTask, StatementTask,
│   │                        ProbeKind) and what it answers (Status,
│   │                        VerificationResult, Diagnostic)
│   ├── verifier.py          the interface, plus the soundness screening a
│   │                        backend cannot opt out of
│   ├── mock.py              a deterministic fake, so CI needs no Lean
│   ├── lean4.py             `lake env lean` + the `#print axioms` audit
│   ├── axle.py              plumbing for an HTTP verification service
│   ├── soundness.py         screening source for what makes a verdict
│   │                        hollow. Answers are screened for all 27
│   │                        patterns, problem statements for the 18 that
│   │                        apply to a statement
│   └── comments.py          what counts as a comment, per language. Scanner
│                            based, so text inside a string literal is not
│                            mistaken for code
│
├── proving/               API 1: does this answer prove this theorem?
│   ├── matching.py          STAGE 1: is this the same theorem, actually proved?
│   ├── lean_file.py         one Lean file -> declarations, with signatures,
│   │                        bodies, attributes and answer holes
│   ├── analysis/            what to measure about a proof
│   │   ├── measure.py         fills a verdict's metrics in. The backend
│   │   │                      reports what the prover said and nothing more.
│   │   ├── tactics.py         which tactics, how often
│   │   ├── structure.py       lemmas, dependency depth, comments, duplication
│   │   ├── modes.py           why a proof failed, or how it succeeded
│   │   ├── statement_metrics.py  a statement's shape, used as a difficulty axis
│   │   └── stats.py           distributions, point-biserial correlation
│   └── grading/             STAGES 2 and 3, and the pipeline over all three
│       ├── pipeline.py        stage sequencing; NOT_RUN as a real outcome
│       ├── stages.py          the stage definitions and GradedAnswer
│       ├── compiling.py       driving the prover over a benchmark
│       ├── statistics.py      over correct *and* incorrect answers
│       ├── metadata.py        per-problem provenance; Lean/Mathlib versions
│       ├── problem_health.py  is the problem set fit to grade? assumes_nothing
│       │                    (text) + elaborates / non_trivial / non_vacuous /
│       │                    gold_equivalent (the prover)
│       ├── artifacts.py       writing the results directory
│       └── contest.py         ProblemSet, Submission, grade_contest
│
└── autoformalization/     API 2: is this statement faithful?
    ├── checker.py           prover probes + an LLM judge, kept distinct
    ├── types.py             statement verdicts and judge records
    ├── judge.py             the judge interface, consensus, abstention
    └── judges/claude.py     the Claude judge
```

Where a module goes, as a rule you can apply without asking anyone:

* it **imports** the APIs → top level (`cli.py`, `registry.py`)
* it is how you talk to a prover, or how you screen what you hand one →
  `backends/`
* only one API uses it → inside that API

There is deliberately no folder for "things more than one place uses". Two
have existed, `shared/` and `source/`, and both were named for who used
them rather than for what they were — so both stopped describing their
contents the moment the callers changed.

`tests/test_layering.py` walks every import, including the indented ones,
and fails on any that does not go downward. It exists because the rule was
broken twice while nobody was checking, and one of those breaks hid a bug
(see stage 3 below).

## The three stages

Grading is three stages, and each one's conclusion is recorded.

### Stage 1 — match (text only, no prover)

Is the submitted file an answer to *this* problem? Cheap, so it runs first
and can refuse before a prover is started.

* the target theorem is present, with the same statement
* every `answer(...)` hole is filled, and no new one was introduced
* nothing the statement depends on was redefined underneath it
* no helper the proof cites is left unproved
* 27 reward-hacking patterns, each labelled `[class:pattern_id]`

A refusal here is final. A pass here is **not** a verdict — it means the
text is honest, not that the proof works.

### Stage 2 — compile (the prover)

The authoritative step. The probe states the **problem's** proposition,
closes it with the **answer's** proof term, over the **problem's** own
definitions — so a gutted definition in the answer cannot help it.

Then `#print axioms` on the result: `sorry` compiles with a warning, and
`axiom cheat : <goal>` compiles with no complaint at all. The kernel is
not the bar; the axiom listing is.

If the prover disagrees with stage 1, **the prover wins**. If stage 2
cannot run, the outcome is `NOT_RUN` — never folded into passed or failed.

### Stage 3 — report (statistics)

Over correct *and* incorrect answers, because the incorrect ones carry
most of the signal. Per-problem, per-participant, per-reason, plus a
correlation search over every numeric metric.

Metrics are collected for **every** answer, including those stage 1
refused — those never reach a prover, so nothing else would record their
shape. This is worth stating because for a while it was not true: the
metrics were computed inside `verify()`, grading calls `probe()`, and the
result was a contest run that recorded pass/fail and nothing to analyse.
Measuring is now its own step, `proving/analysis/measure.py`.

## The results directory

```
results/<run-id>/            run id is a UTC timestamp, e.g. 2026-09-29T0312Z
├── run.json                 the run: versions, hashes, counts, warnings
├── problems.json            per-problem metadata, including the MathDB id
├── problems.tsv             the same, for reading
├── leaderboard.tsv          who solved what
├── summary.txt/.json        the headline numbers
├── 1-match/
│   ├── all.jsonl              every stage-1 verdict
│   └── refused.jsonl          just the refusals, with the trick named
├── 2-compile/
│   ├── all.jsonl              every stage-2 verdict
│   ├── probes/<p>/<prob>.lean the exact source the prover was given
│   └── logs/<p>/<prob>.log    the prover's own output
├── 3-report/
│   ├── by-problem.tsv
│   ├── by-participant.tsv
│   └── reasons.tsv
└── answers/<participant>/<problem>.json   everything known about one answer,
                                           metrics included
```

Naming rules: stage directories are numbered in run order; `.jsonl` is a
stream, `.json` is one object, `.tsv` is for a human; ids are path
components so a shell glob works; probes are kept so a verdict can be
re-run by hand.

## What this cannot tell you

* **Nothing here has run against a real Lean toolchain or real model
  output.** The 27 reward-hacking patterns are implemented and tested
  against fixtures; they are not an empirical finding about what models do.
* A judge's verdict is an opinion. `checked_faithfulness` records whether
  one was even obtained, and the summary says so rather than implying a
  clean bill of health.
* Stage 1 trades false positives for zero misses. It will occasionally
  refuse an honest answer; the adversarial suite exists to keep the miss
  count at zero, not the noise at zero.
