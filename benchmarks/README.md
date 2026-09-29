# Benchmarks

One benchmark is **one folder of Lean files**. A 100-problem set is a
folder with 100 files in it, one theorem per file.

```
benchmarks/
└── demo-2026/                  one benchmark
    ├── benchmark.json          name, version, pinned toolchain, conventions
    ├── problems/               the setter's problems; the file stem is the id
    │   ├── P001.lean
    │   ├── P002.lean
    │   └── P003.lean
    ├── submissions/            one directory per participant
    │   ├── alice/
    │   │   ├── P001.lean       named after the problem it answers
    │   │   └── P002.lean
    │   ├── bob/
    │   └── carol/
    └── results/                one directory per grading run
        └── 2026-09-29T0312Z/
```

Grade it:

```bash
ftp-eval grade \
  --problems benchmarks/demo-2026/problems \
  --submissions benchmarks/demo-2026/submissions \
  --out benchmarks/demo-2026/results \
  -b lean4 -o project_dir=~/mathlib-project
```

Without `-b` the run stops after stage 1 and says so, rather than implying
the proofs were checked.

## Why a folder of files, and not one big JSONL

Because the unit participants work in is a file. A `.lean` file opens in
their editor, compiles with `lake env lean`, and is what they hand back —
so the problem set being files means there is no serialization step where a
statement can be mangled, and no ambiguity about what was set.

It also makes the problem set diffable and reviewable in git, which matters
when a problem has to be corrected mid-contest: the change is a commit, and
`problems.tsv` in every results directory records the content hash that was
actually graded.

## The conventions a problem file must follow

| rule | why |
|---|---|
**One theorem per file.** | The problem id is the file; grading never has to guess which theorem is under test. A file with several statements is reported, not silently resolved. |
**The file stem is the problem id.** | `P001.lean` ↔ `P001`. Participants name their answers the same way; a file named after nothing is reported, never dropped. |
**`sorry` is the body of an unproved statement.** | That is how an open problem is *stated*. It is not a violation in the problem file. |
**`answer(sorry)` marks a value to be supplied.** | `= answer(sorry)` becomes `= answer(4)` in the answer. The hole is expected to change; nothing else is. |
**Definitions the statement uses are part of the problem.** | An answer must return them unchanged. Redefining one changes the claim while leaving the theorem's text identical. |

## What participants may and may not do

May:

* add as many of their own definitions and helper lemmas as the proof needs
* reformat, rewrap, and add comments
* fill every `answer(...)` hole

May not:

* change the theorem's signature — binders, types, bounds, `=` vs `≤`
* redefine any definition the problem supplied
* leave a helper unproved that the main proof cites
* declare an axiom, use `native_decide`, `#exit`, `set_option maxHeartbeats 0`,
  or any of the other tricks in [../docs/soundness.md](../docs/soundness.md)
* declare the theorem under a different namespace, or twice

Each of these is checked, and each has a test that it is caught — see
`tests/test_matching_adversarial.py`.

## The results directory

Every stage's conclusion is recorded, so a disputed result is answerable
months later without re-running anything. Layout and naming rules are
documented in `src/ftp_eval/grading/artifacts.py`; the short version:

```
results/2026-09-29T0312Z/
├── run.json              what was graded, with what, when
├── problems.tsv          problem ids + content hashes
├── leaderboard.tsv       the ranking
├── summary.txt/.json     stage 3
├── 1-match/              every stage-1 verdict, and the refusals alone
├── 2-compile/            every stage-2 verdict, plus the exact probe
│   ├── probes/<participant>/<problem>.lean     source handed to the prover
│   └── logs/<participant>/<problem>.log        what it said back
├── 3-report/             by-problem, by-participant, reasons
└── answers/<participant>/<problem>.json        all three stages, one answer
```
