# FTP Eval

**Formal Theorem Proving Evaluation** — a Python library and CLI for evaluating
Lean 4 answers against frozen targets, with kernel verification, axiom audits,
and reproducible run reports.

This repository provides the evaluator. The companion
[MathDB-Lean Problems](https://github.com/mathdb-lean/problems) repository imports
formal statements, records conversion and review evidence, and publishes
compatible benchmarks. FTP Eval also accepts independently authored benchmarks.
The evaluator consumes answer files supplied by a model or participant;
experiment metadata identifies the producing system.

## Quick start

Use Python **3.10 or newer**; use 3.11 or newer when also working with the problem
library. The core Python package has no third-party runtime dependencies.

```sh
git clone https://github.com/mathdb-lean/formal-theorem-proving-eval-api.git
cd formal-theorem-proving-eval-api
python -m pip install -e .
ftp-eval --help
```

Run the bundled demo without a Lean installation:

```sh
ftp-eval grade --problems benchmarks/demo-2026/problems --submissions benchmarks/demo-2026/submissions --out runs
```

This exercises interface checks and saves reports. **Zero answers count as
solved when no verification backend runs.** The demo intentionally includes
valid answers, failed proofs, an honest non-answer, and a misfiled submission.

## The answer contract

The organizer supplies a trusted module defining `Problem.Target`. It is
compiled before the answer is loaded:

```lean
-- FtpEvalBench/P001.lean
namespace Problem
abbrev Target (n : Nat) : Prop := n % 7 = 3 ∧ n % 11 = 5
end Problem
```

A participant supplies one `.lean` file and exports `Submission.solution`:

```lean
-- submissions/alice/P001.lean
import FtpEvalBench.P001

namespace Submission
def value : Nat := 38
theorem solution : Problem.Target value := by decide
end Submission
```

For a proof task, the type is simply `Submission.solution : Problem.Target`.
For a value or construction task, it is `Problem.Target value`; values may also
be dependent functions or predicates. Answers may organize their own helpers
within the benchmark's policy.

When fixed gold arguments are supplied, a trusted Goal module freezes them
before importing the answer. Without fixed gold, a construction task can accept
any witness satisfying the frozen target. A generated Check module ascribes the
solution to the required type and audits its transitive axiom dependencies.
See [the benchmark contract](benchmarks/README.md) and
[the frozen-target example](examples/frozen-target/).

## Benchmark and submission layout

```text
my-benchmark/
  benchmark.json
  problems/
    P001.lean
    P002.lean

submissions/
  alice/
    P001.lean
    P002.lean
  bob/
    P001.lean
```

Problem IDs are the Lean filename stems. Each participant directory contains
answers named after those IDs. `benchmark.json` declares modules, optional gold,
policy, and environment pins. MathDB-Lean exports use task UUIDs as filenames and
retain readable labels in the manifest. Missing answers and unrecognized files
are reported separately.

## Evaluate submissions

For local development with trusted answers, use a built Lake project matching
the benchmark's Lean and dependency pins:

```sh
ftp-eval grade --problems benchmarks/demo-2026/problems --submissions benchmarks/demo-2026/submissions -b lean4 -o project_dir=/path/to/built-project --out runs --run-metadata model=my-model --run-metadata seed=42
```

The demo's declared environment is in
[benchmark.json](benchmarks/demo-2026/benchmark.json). Each benchmark has its own
pins; the companion problem library can use a different Lean/Mathlib version.
`--run-metadata KEY=VALUE` records experiment labels. `--strict` exits non-zero
if any submitted answer is not established as solved; the demo intentionally
contains such answers.

**Use `lean4-docker` for untrusted or public submissions.** Prepare a built
organizer image and the exact manifest pins described in
[Docker worker setup](docs/container-workers.md), then replace `IMAGE_ID` below
with the image's 64-character SHA-256 ID:

```sh
ftp-eval grade --problems /path/to/benchmark/problems --submissions /path/to/submissions -b lean4-docker -o image=sha256:IMAGE_ID --out runs --strict
```

Docker grading uses read-only Linux containers, no network, resource limits,
environment preflight, and mandatory fresh kernel replay. Native `lean4` runs as
the grading user and records `safe = "none"`; separate build directories do not
provide containment. The guarantees and remaining limits are documented in the
[soundness policy](docs/soundness.md).

The grading stages are:

```text
environment → interface → kernel → replay → dependencies → axioms → report
```

A solved verdict requires kernel verification, an allowed axiom closure, and
the checks required by the selected backend and policy. Missing verification
evidence cannot establish a solution. A valid Lean proof establishes the formal
target; fidelity to the mathematical source is a separate review question.

## Python API

```python
from ftp_eval import create, grade_contest, load_problem_set, load_submissions

problems = load_problem_set("benchmarks/demo-2026/problems")
answers = load_submissions("benchmarks/demo-2026/submissions", problems)

with create("lean4", project_dir="/path/to/built-project") as backend:
    result = grade_contest(
        problems,
        answers,
        verifier=backend,
        output_dir="runs",
        run_metadata={"model": "my-model", "seed": 42},
    )
```

The same interface accepts the `problems/` directory of a
[MathDB-Lean release](https://github.com/mathdb-lean/problems#benchmark-format).
Use `create("lean4-docker", image="sha256:...")` for container grading. See
[architecture](ARCHITECTURE.md) for benchmark contracts and backend extension points.

## Results and reproducibility

Each invocation writes a new `runs/<run-id>/` directory:

```text
runs/<run-id>/
  run.json              lifecycle, policy, environment, experiment labels
  inputs/               exact problem and answer sources
  events.jsonl          stage-by-stage checkpoints
  answers/              per-answer verdicts and analysis
  summary.json
  summary.txt
  leaderboard.tsv
```

Stage directories also retain generated modules, logs, and refusal reasons.
Inputs are saved before grading; completed answer records are saved before the
next answer starts. Existing run IDs are refused. Interrupted runs retain
checkpoints for inspection; automatic resume is not implemented.
See [run records](docs/runs.md) and [metrics](docs/metrics.md). Analysis metrics
describe answers and outcomes; they do not establish proof correctness.

## Statement audits and diagnostics

```sh
ftp-eval backends
ftp-eval judges
ftp-eval doctor -b lean4 -o project_dir=/path/to/built-project
ftp-eval audit --problems /path/to/benchmark/problems
```

Statement audits check provenance; a configured backend adds compilation and
health probes, and a configured judge assesses correspondence to supplied prose.
Failed tactic search is inconclusive. The optional Claude judge requires
`python -m pip install -e ".[judge]"` and explicit `--yes` authorization for paid
API calls. Judge output is not a proof verdict.

`mock` and `axle` provide lower-level verifier integrations; they do not establish
frozen-module benchmark proofs without the required build capability. To add a
backend, follow [the integration guide](docs/adding-a-backend.md).

## Development

```sh
python -m pip install -e ".[dev]"
pytest
ruff check src tests scripts
mypy
python scripts/measure_metrics_speed.py --check
```

Default tests run without Lean. Real Lean and Docker checks are opt-in; see
[test setup](tests/README.md). The performance script measures Python overhead,
not Lean compilation time.

- [src/ftp_eval/](src/ftp_eval/): installed library, CLI, backends, and analysis.
- [benchmarks/](benchmarks/): demo, task-shape, and refusal fixtures.
- [examples/](examples/): a complete frozen-target interface example.
- [tests/](tests/): interface, grading, backend, analysis, and audit regressions.
- [scripts/](scripts/): performance checks and Docker smoke tests.
- [docs/](docs/): run, policy, worker, and integration references.

## License

Copyright 2026 The mathdb-lean Authors. Licensed under the
[Apache License 2.0](LICENSE).

Releases up to and including 0.1.0 were published under the MIT License. That
grant is not withdrawn for the versions that carried it.
