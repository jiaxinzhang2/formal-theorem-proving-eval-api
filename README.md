# formal-theorem-proving-eval-api

Evaluate theorem-proving submissions against a **frozen Lean proposition**.
The benchmark compiles `Problem.Target` first. An answer imports that module,
develops any helpers it needs, and exports `Submission.solution : Problem.Target`.
A generated check module checks the type and audits the proof's axiom closure.

Only a successful kernel check **and** a clean axiom audit count as solved.
Missing backends, unsupported module builds, and missing axiom listings never
count as proof. Textual theorem matching and the `match` command have been removed.

## Install

```bash
pip install -e ".[dev]"
```

Python >=3.10. The package itself uses only the standard library.
Lean grading additionally requires a configured, built Lake project.

## The interface

```lean
-- Bench/P001.lean: trusted, compiled before any answer
namespace Problem
def Target (n : Nat) : Prop := n = 1
end Problem
```

```lean
-- The answer may freely organize definitions, lemmas and instances.
import Bench.P001
namespace Submission
def value : Nat := 1
lemma helper : value = 1 := rfl
theorem solution : Problem.Target value := helper
end Submission
```

The generated `Check.lean` imports the problem and answer, states the required
target type, cites `Submission.solution`, and prints its axioms. Helpers are
never compared against the problem's declarations. See
[the complete example](examples/frozen-target/) and [architecture](ARCHITECTURE.md).

## Grade a benchmark

```bash
ftp-eval grade --problems benchmarks/demo-2026/problems \
  --submissions benchmarks/demo-2026/submissions \
  -b lean4 -o project_dir=/path/to/built-lean-project \
  --out runs --run-metadata model=my-model --run-metadata seed=42 --strict
```

The CLI saves results under `runs/<run-id>/` by default. Each run has a unique id;
an existing id is rejected. `--run-metadata KEY=VALUE` records caller-supplied
experiment labels, such as API/model version, temperature and seed. It does not
invoke a model API: this package evaluates the answers already in `submissions/`.

For a toolchain-free demonstration, omit `-b` or use `-b mock`. The interface
screen and reports still run, but solved stays zero. `--strict` exits non-zero
if any submitted answer is not established as solved. Missing answers and
unrecognized files are counted separately.

```python
from ftp_eval import create, grade_contest, load_problem_set, load_submissions

problems = load_problem_set("benchmarks/demo-2026/problems")
answers = load_submissions("benchmarks/demo-2026/submissions", problems)
with create("lean4", project_dir="/path/to/built-lean-project") as backend:
    result = grade_contest(
        problems, answers, verifier=backend, output_dir="runs",
        run_metadata={"model": "my-model", "seed": 42},
    )
```

## Every stage is saved before the next one starts

The run manifest and source inputs are saved before grading. Every answer emits
running and terminal checkpoints for **interface → kernel → axioms**. An
append-only event history and per-answer snapshots preserve progress across
interruptions. Final answer records are saved before advancing to another answer.
Aggregate reports are saved last.

See [run lifecycle and output format](docs/runs.md) and
[benchmark format and policy](benchmarks/README.md).

## Independent statement auditing

`ftp-eval audit --problems statements/` checks original formal statements for
provenance, faithfulness to prose (with `--judge`), and health (with `--backend`).
This is a separate API from grading frozen Target modules. Health probes expect
the original theorem statement, rather than a `def Target` wrapper.

Judges: `mock` for tests and `claude` for API judging. A paid judge requires
`--yes`; `--judge-option KEY=VALUE` configures it. The judge does not prove answers.

## Backends and diagnostics

```bash
ftp-eval backends
ftp-eval judges
ftp-eval doctor -b lean4 -o project_dir=/path/to/project
```

`lean4` supports isolated multi-module builds with fresh Lean processes.
`mock` and the generic `axle` HTTP adapter support the lower-level verifier API,
but do not provide multi-module grading unless a backend implements that capability.
See [adding a backend](docs/adding-a-backend.md).

Proof structure, tactics, failure/success modes and statement complexity are
recorded by the analysis layer. Metrics do not establish correctness; see
[metrics reference](docs/metrics.md) and [soundness policy](docs/soundness.md).

## Validation

```bash
pytest
ruff check src tests
python tests/measure_speed.py --check
```

Tests use synthetic module-build responses to exercise the grading and persistence
contracts without Lean. A passing Python suite does not establish that a real
Lean toolchain compiled the examples. The performance script measures Python
analysis and grading overhead; it does not benchmark real Lean compilation.

## License

MIT. See [LICENSE](LICENSE).
