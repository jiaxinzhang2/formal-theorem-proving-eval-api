# Architecture

The proving contract is one frozen `Problem.Target` and one exported
`Submission.solution`. There is no theorem-text matching or proof-body extraction
in the grading pipeline.

## Three dependency layers

```text
proving/                    autoformalization/
interface + grading         judge-only faithfulness
analysis + Lean parsing
             ↓                    ↓
backends/     prover contracts, adapters, diagnostics, source screens
                          ↓
spec/         benchmark/artifact contracts and stage vocabulary
```

The two APIs do not import each other. Top-level CLI, registry and public exports
compose them. `tests/test_layering.py` checks both relative and absolute imports
with Python's AST, including imports nested inside functions.

## Source map

```text
src/ftp_eval/
  cli.py                  grade, audit, backends, judges, doctor
  registry.py             backend and judge registration
  io.py                   JSONL input/output
  spec/
    benchmark.py          Benchmark and BenchmarkProblem
    artifacts.py          ArtifactWriter
    stage.py              the single StageId / StageStatus definitions
  backends/
    types.py              ModuleSource, ModuleBuild and verifier data
    verifier.py           low-level prover interface
    lean4.py              ordered module builds and fresh processes
    mock.py, axle.py       lower-level test / HTTP backends
    soundness.py           source screening and axiom audit
    comments.py            comment scanner
  proving/
    interface.py          frozen target, policy, kernel ascription, axiom audit
    lean_file.py          lightweight source parser (not a Lean elaborator)
    grading/
      contest.py          load benchmark/submissions; grade each answer
      stages.py           interface verdict plus proof metrics
      artifacts.py        durable stage checkpoints and run outputs
      metadata.py         provenance, manifest, toolchain reconciliation
      statistics.py       aggregate all answers, including refusals
      problem_health.py   independent original-statement health probes
    analysis/             metrics and failure/success classification
  autoformalization/      faithfulness judges, voting, verdicts
```

## Execution and persistence

1. Load the benchmark, submissions, explicit policy and gold arguments.
2. Allocate a new run, freeze inputs and save the run manifest.
3. For each answer, save interface status before proceeding.
4. Save the exact problem, answer and generated check modules before kernel work.
5. Build in dependency order: frozen problem, answer, generated check.
6. Save the kernel result before auditing its axiom listing.
7. Save the axiom result and final answer record before advancing.
8. Aggregate metrics and leaderboard; save report and mark the run complete.

Running stages are recorded too, so interruptions expose the unfinished step.
Ordinary backend exceptions are recorded per answer and do not discard the group.
Keyboard interruption stops the run and marks it interrupted. Hard termination
may leave the run marked running; its latest checkpoint still identifies progress.

An unsupported module backend produces NOT_RUN. Kernel acceptance without an
axiom listing produces an audit failure. Neither counts as solved.

## Policy and trusted environment

The manifest records allowed imports, allowed axioms and global-instance policy.
Instances may help prove the frozen proposition. The allowlist, audit, toolchain
and trusted Lake project jointly define the environment; this is not an OS sandbox
for arbitrary Lean metaprograms. Each answer gets a unique build directory and
fresh Lean processes. Only trusted problem modules are cached; cache keys include
module name, source, compiler options and pinned Lake/toolchain metadata.

Read [benchmark format](benchmarks/README.md), [run outputs](docs/runs.md),
[backend contract](docs/adding-a-backend.md) and [tests](tests/README.md).
