# Architecture

The proving contract is one frozen `Problem.Target` and one exported
`Submission.solution`. There is no theorem-text matching or proof-body extraction
in the grading pipeline.

## Three dependency layers

```text
proving/                    autoformalization/
checking + running          judge-only faithfulness
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
  jsonl.py                JSONL input/output
  spec/
    benchmark.py          Benchmark and BenchmarkProblem
    artifacts.py          ArtifactWriter
    stage.py              the single StageId / StageStatus definitions
  backends/
    types.py              ModuleSource, ModuleBuild and verifier data
    verifier.py           low-level prover interface
    lean4.py              ordered module builds and native Lean tool calls
    lean4_docker.py        Lean adapter dispatching all tools to a worker
    lean_diagnostics.py   Lean log parsing and exact axiom listings
    execution/
      base.py             immutable jobs, results and cloud executor contract
      docker.py           bounded Docker execution and artifact validation
    mock.py               lower-level test backend
    axle.py               lower-level HTTP backend
    soundness.py           source screening and axiom audit
    comments.py            comment scanner
  proving/
    lean_file.py          lightweight source parser (not a Lean elaborator)
    checking/
      interface.py        frozen interface, report and authoritative verdict
      screening.py        parse the interface and refuse forbidden answers
      policy.py           allowed imports, axioms, instances and replay policy
      modules.py          trusted Goal and generated Check sources
      evaluator.py        single-submission verification stages
    running/
      inputs.py           load folders and snapshot benchmark/submissions
      manifest.py         declared benchmark settings and policy loading
      provenance.py       per-problem source and metadata
      environment.py      observe and validate the actual toolchain
      recording.py        checkpoint checking verdicts and attach measurements
      pipeline.py         preflight, answer loop and report lifecycle
      results.py          answer, participant and benchmark result types
      run_directory.py    durable stage checkpoints and folder outputs
      summary.py          aggregate all answers, including refusals
      problem_health.py   frozen-target universal probes and legacy theorem probes
    analysis/
      metrics.py          attach proof and statement measurements to verdicts
      classification.py   success/failure labels and attribution
      statistics.py       distributions and outcome correlations
      tactics.py          tactic extraction and usage summaries
      proof_structure.py  proof shape and repetition measurements
      statement_metrics.py proposition complexity measurements
  autoformalization/
    checker.py          check a formalization against the problem it came from
    judge.py            the judge contract and multi-judge voting
    judges/claude.py    faithfulness judge backed by the Claude API
    types.py            this API's verdicts and per-judge reports
```

`checking` decides whether a proof satisfies the frozen target. Its evaluator
runs the stages, and `checking/interface.py` defines the single `solved` rule.
`running` organizes a benchmark and records results. `running/recording.py`
delegates the check, saves sources/events and attaches metrics; its `GradedAnswer`
inherits that same rule without overriding it. `analysis` describes proofs and
outcomes; it never grants acceptance.

The policy has one semantic owner, `checking/policy.py`. `running/manifest.py`
loads the benchmark's choices into that policy; it does not implement another
policy check. Backend and autoformalization `types.py` files hold the distinct
data contracts of those APIs. Proof measurements live in `analysis/proof_structure.py`,
statement measurements in `analysis/statement_metrics.py`, and `analysis/metrics.py`
combines them. `analysis/statistics.py` supplies distributions/correlations;
`running/summary.py` aggregates benchmark verdicts.

Import concrete modules inside the implementation and use `ftp_eval`,
`proving.checking` or `proving.running` for public exports.

Source lives under `src/` so the repository root is separate from the installed
Python package. `scripts/` contains developer entry points that use that package:
`measure_metrics_speed.py` measures Python overhead, and `smoke_docker.py`
checks a real organizer image. Tests mirror the source responsibilities in
`tests/checking`, `tests/running`, `tests/backends`, `tests/analysis` and
`tests/autoformalization`; CLI and dependency-layer checks stay at the test root.

The earlier internal paths `proving.interface`, `grading.contest`,
`grading.metadata`, `backends.container`, `analysis.measure`, `analysis.modes`,
`analysis.stats` and `ftp_eval.io` have been replaced by the owners above.
Top-level `ftp_eval` exports, CLI commands and persisted run schemas are unchanged.

## Execution and persistence

1. Load the benchmark, submissions, explicit policy and gold arguments.
2. Allocate a new run, freeze inputs and save the run manifest.
3. Save environment preflight: exact pins and cross-module build capability.
4. For each answer, save interface status before proceeding.
5. Save the exact problem, optional trusted Goal, answer and check modules.
6. Build in order: frozen problem, value Goal, answer, generated check. Gold
   expressions elaborate before the answer exists; open value problems use a
   frozen existential goal. The check cites a constant.
7. Save compile, fresh kernel replay and dependency-extraction results separately.
8. Apply axiom policy and save the final answer before advancing.
9. Aggregate metrics and leaderboard; save report and mark the run complete.

Running stages are recorded too, so interruptions expose the unfinished step.
Ordinary backend exceptions are recorded per answer and do not discard the group.
Keyboard interruption stops the run and marks it interrupted. Hard termination
may leave the run marked running; its latest checkpoint still identifies progress.

Failed module capability preflight aborts before answer grading. A toolchain-free
development run records NOT_RUN. Kernel acceptance without an
axiom listing produces an audit failure. Neither counts as solved.

## Policy and trusted environment

The manifest records allowed imports, allowed axioms and global-instance policy.
Instances may help prove the frozen proposition. The allowlist, audit, toolchain
and trusted Lake project jointly define the environment. Native `lean4` has no OS
sandbox; `lean4-docker` uses read-only Linux workers and requires strict preflight
and fresh replay. Each answer gets a unique build directory and
fresh Lean processes. Only trusted problem modules are cached; cache keys include
module name, source, trusted dependency sources, compiler options and pinned
Lake/toolchain/image metadata, and each cached artifact carries a digest so a
hit is the file that key described rather than a file of that name.
`Benchmark` inputs are snapshotted once, and an
injected `ArtifactWriter` receives every checkpoint; folder storage is optional.

`evaluate_benchmark` and `evaluate_submission` return frozen-interface acceptance.
`Verifier.verify_attempt` only checks a lower-level proof attempt and does not
promise the benchmark's frozen environment, value or policy guarantees.
Default problem modules use `FtpEvalBench`; preflight refuses project-library
prefix collisions and probes the actual roots using nested imports. The lexical
source reader handles ordinary literal `lean_lib` declarations; the real import
probe remains the final capability check for dynamically configured projects.

See [container workers](docs/container-workers.md) for the AWS executor boundary.

The Docker adapter inherits staging and module orchestration, but overrides the
tool runner: `_build_one -> _run_lean -> self._run_tool -> ContainerExecutor.run`.
Capability probes, compilation, replay and dependency extraction all dispatch
through that boundary. Host directories hold source inputs and returned artifacts;
they are never used as a native Lake project by this adapter.

Read [benchmark format](benchmarks/README.md), [run outputs](docs/runs.md),
[backend contract](docs/adding-a-backend.md) and [tests](tests/README.md).
