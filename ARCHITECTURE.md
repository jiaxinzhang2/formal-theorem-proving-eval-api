# Architecture

Start here. The contracts are in [`src/ftp_eval/spec/`](src/ftp_eval/spec/) —
pure interface, no logic. Read that package and you have the design.

## The repository

```
formal-theorem-proving-eval-api/
├── ARCHITECTURE.md      ← you are here: the map
├── README.md               what it is and how to run it
│
├── benchmarks/             PROBLEM SETS. one folder = one benchmark
│   ├── README.md              the benchmark format spec
│   └── demo-2026/
│       ├── benchmark.json     version + pinned toolchain
│       ├── problems/          P001.lean … P100.lean  (one theorem each)
│       ├── submissions/       one directory per participant
│       └── results/           one directory per grading run
│
├── examples/               RUNNABLE FIXTURES
│   ├── answer-verdicts/       one problem, six answers, six verdicts
│   └── jsonl-mode/            the other input mode: datasets, pass@k
│
├── docs/                   REFERENCE
│   ├── soundness.md           every reward-hacking check, and its limits
│   ├── metrics.md             every metric, by category, with its cost
│   └── adding-a-backend.md    how to plug in a prover
│
├── tests/                  455 tests
│   ├── test_matching_adversarial.py   attacks on the grader
│   ├── test_performance.py            complexity guarantees
│   └── measure_speed.py               the speed numbers CI enforces
│
└── src/ftp_eval/           THE PACKAGE
```

`benchmarks/` means **problem sets**, never speed benchmarks — the speed
measurements live in `tests/measure_speed.py`, beside the scaling tests
whose budgets they share.

## The package

```
ftp_eval/
├── spec/               THE CONTRACTS — read this first
│   ├── benchmark.py       what a Benchmark and a SubmissionSource are
│   ├── stage.py           what a GradingStage is (all three share it)
│   └── artifacts.py       what a results directory must record
│
├── formalizing/        ① is the formalization faithful to the problem?
│   ├── lean_file.py       splitting a Lean file into declarations
│   ├── matching.py        does answer.lean state theorem.lean?
│   ├── checker.py         prover probes + an LLM judge
│   ├── judge.py           judge interface, mock, consensus voting
│   └── judges/            provider-backed judges
│
├── proving/            ② does the proof close the goal?
│   ├── verifier.py        the interface every backend implements
│   ├── runner.py          batch execution: streaming, resume, caching
│   └── backends/          mock, lean4, axle
│
├── analysis/           ③ what happened — passes and failures alike
│   ├── tactics.py         which tactics, in what order
│   ├── structure.py       proof shape, statement complexity, correlations
│   ├── modes.py           failure modes + attribution; success modes
│   └── scoring.py         unbiased pass@k, the Summary
│
├── grading/            THE CONTEST PIPELINE — runs ①→②→③
│   ├── stages.py          one answer's trip through all three
│   ├── compiling.py       stage ②
│   ├── statistics.py      stage ③
│   ├── metadata.py        problem provenance + toolchain reconciliation
│   ├── artifacts.py       the results folder
│   └── contest.py         the driver
│
├── types.py            shared vocabulary  ─┐
├── soundness.py        reward hacking       │ used by every layer
├── comments.py         comment handling     │
├── dataset.py          JSONL I/O            │
├── registry.py         lookup by name      ─┘
├── end_to_end.py       the DATASET pipeline (JSONL, many samples, pass@k)
└── cli.py              ftp-eval
```

## The two ways in

They share every layer below them and differ only in input shape.

| | contest | dataset |
|---|---|---|
input | folders of `.lean` files | JSONL: prose + statement + proofs |
unit | one theorem per file, one file per problem | one task, k sampled attempts |
question | did this participant prove this problem? | what is this model's pass@k? |
driver | `grading/contest.py` | `end_to_end.py` + `proving/runner.py` |
command | `ftp-eval grade` | `ftp-eval verify` / `eval-all` |

## The three stages

Every answer goes through the same three, in order.

```
answer.lean
    │
    │  ① MATCH     does it state the theorem that was set?
    │              text only, no prover, milliseconds
    │              → formalizing/matching.py
    ▼
the right theorem, with a proof attached
    │
    │  ② COMPILE   does the prover accept that proof — of the
    │              THEOREM's proposition, over the THEOREM's definitions?
    │              → grading/compiling.py → proving/backends/lean4.py
    ▼
a verdict per answer
    │
    │  ③ REPORT    what happened, across every answer,
    │              correct and incorrect alike
    │              → grading/statistics.py → analysis/
    ▼
scores, failure modes, per-problem difficulty
```

**They stop early.** `GradingStage.requires` declares the ordering, so a
stage that did not pass blocks the next one. Compiling an answer that does
not state the theorem is wasted work — and a compile that *succeeds* on the
wrong theorem reads like a pass, which is the confusion this whole design
exists to remove.

**Stage ③ takes everything.** Answers refused at stage ① are as much a part
of the statistics as the ones that passed. "12 answers restated the theorem"
and "30 failed to compile" call for completely different responses, and a
pipeline that discarded the early failures could report neither.

**A stage that could not run says so.** `StageStatus` has `NOT_RUN`
alongside `PASSED` and `FAILED`, and it is never collapsed into either.
Without a prover, `solved` means "stage ① passed" — not that any proof was
checked — and every report states that explicitly.

## The one idea underneath all of it

**Passing the kernel is not the bar.** `sorry` compiles with a warning.
`axiom cheat : <the goal>` compiles with no complaint at all. A proof from
contradictory hypotheses is *genuinely valid* and proves nothing. A file
that compiles perfectly may have restated the theorem.

So: stage ① checks *what* is being proved before stage ② checks *whether*
it is proved, the reward-hacking screen sits above every backend and cannot
be opted out of, and the confirmation probe is built from the **theorem
file's** definitions rather than the answer's — because an answer compiled
against its own gutted vocabulary would confirm a claim nobody asked for.

Details: [docs/soundness.md](docs/soundness.md).
