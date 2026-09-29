# Architecture

## One input format

Lean files. That is the whole input contract:

| thing | is | example |
|---|---|---|
| a **problem** | one `.lean` file, one theorem | `problems/P001.lean` |
| an **answer** | one `.lean` file | `submissions/alice/P001.lean` |
| a **benchmark** | a folder of problems | `benchmarks/demo-2026/` |
| a **submission** | a folder per participant | `submissions/alice/` |

There is no second input shape. No task JSONL, no attempt records, no
separate statement file.

## Two APIs, because they answer different questions

```
natural language  ──▶  formal statement  ──▶  formal proof
                  └────────┬───────────┘  └────────┬──────┘
                   autoformalization/         proving/
                   is it faithful?          does it prove it?
```

They are separate on purpose. Grading a contest never asks whether the
statement is faithful, and auditing a problem set never asks whether
anybody proved it. Fusing them into one verdict is how a suspicious
statement with a valid proof gets counted as solved.

| | `proving/` | `autoformalization/` |
|---|---|---|
| asks | does this answer prove this theorem? | does this statement mean what the prose says? |
| input | problem `.lean` + answer `.lean` | problem `.lean` (+ its `prose:` metadata) |
| run when | answers come in | **before** publishing the problems |
| command | `ftp-eval match`, `ftp-eval grade` | `ftp-eval audit` |
| needs | a prover | a prover *and* an LLM judge |

## The folder tree

```
src/ftp_eval/
│
│  ── above both APIs: these import them ──
├── __init__.py            the public surface; everything is importable from here
├── cli.py                 the six commands
├── registry.py            name -> backend or judge ("lean4", "claude")
├── io.py                  read and write JSONL (what the commands write out)
│
│  ── the contracts ──
├── spec/                  pure interface, no logic. Read this first.
│   ├── benchmark.py         what a benchmark and a problem set must provide
│   ├── stage.py             what a pipeline stage must provide
│   └── artifacts.py         what a results directory must contain
│
│  ── below both APIs: these know about neither ──
├── shared/
│   ├── types.py             the backend contract: what a prover is asked,
│   │                        what it answers
│   ├── comments.py          what counts as a comment, per language
│   └── soundness.py         screening source text for ways to game a checker
│
│  ── API 1: does this answer prove this theorem? ──
├── proving/
│   ├── lean_file.py         parsing a Lean file into declarations
│   ├── matching.py          STAGE 1: is this the same theorem, actually proved?
│   ├── verifier.py          STAGE 2: the backend interface
│   ├── backends/            mock, lean4, axle
│   ├── analysis/            metrics over proofs
│   │   ├── tactics.py         which tactics, how often
│   │   ├── structure.py       lemmas, dependency depth, comments, duplication
│   │   ├── modes.py           why a proof failed, or how it succeeded
│   │   └── stats.py           distributions, point-biserial correlation
│   └── grading/             STAGE 3 and the pipeline that runs all three
│       ├── pipeline.py        stage sequencing, and NOT_RUN as a real outcome
│       ├── stages.py          the stage definitions
│       ├── compiling.py       driving the prover over a benchmark
│       ├── statistics.py      over correct *and* incorrect answers
│       ├── metadata.py        per-problem provenance; Lean/Mathlib versions
│       ├── artifacts.py       writing the results directory
│       └── contest.py         ProblemSet, Submission, grade_contest
│
│  ── API 2: is this statement faithful? ──
└── autoformalization/
    ├── checker.py           prover probes + an LLM judge
    ├── complexity.py        metrics over statements
    ├── types.py             statement verdicts and judge records
    ├── judge.py             the judge interface, consensus, abstention
    └── judges/claude.py     the Claude judge
```

The rule that decides where a module goes:

* it **imports** both APIs → top level (`cli.py`, `registry.py`)
* both APIs import it, and it imports neither → `shared/`
* only one API uses it → inside that API

That rule is why `stats.py` lives under `proving/analysis/` and not in
`shared/`: the statistics over proof metrics are not the statistics over
statement metrics, and only one side aggregates today.

## The three stages

Grading is three stages, and each one's conclusion is recorded.

### Stage 1 — match (text only, no prover)

Is the submitted file an answer to *this* problem? Cheap, so it runs
first and can refuse before a prover is started.

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

If the prover disagrees with stage 1, **the prover wins**.

If stage 2 cannot run, the outcome is `NOT_RUN` — never silently folded
into passed or failed.

### Stage 3 — report (statistics)

Over correct *and* incorrect answers, because the incorrect ones carry
most of the signal. Per-problem, per-participant, per-reason, plus a
correlation search over every numeric metric.

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
└── answers/<participant>/<problem>.json   everything known about one answer
```

Naming rules: stage directories are numbered in run order; `.jsonl` is a
stream, `.json` is one object, `.tsv` is for a human; ids are path
components so a shell glob works; probes are kept so a verdict can be
re-run by hand.

## What this cannot tell you

* **Nothing here has run against a real Lean toolchain or real model
  output.** The 27 reward-hacking patterns are implemented and tested
  against fixtures; they are not an empirical finding about what models
  actually do.
* A judge's verdict is an opinion. `checked_faithfulness` records whether
  one was even obtained, and the summary says so rather than implying a
  clean bill of health.
* Stage 1 trades false positives for zero misses. It will occasionally
  refuse an honest answer; the adversarial test suite exists to keep the
  miss count at zero, not the noise at zero.
