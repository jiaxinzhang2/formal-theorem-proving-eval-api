# Tests

```bash
pytest
ruff check src tests scripts
mypy
python scripts/measure_metrics_speed.py --check
```

The Python suite does not require Lean. Simulated module-build responses exercise
control flow and persistence; they are not real kernel verification.

`checking/test_task_types.py` also has opt-in integration tests. With Lean/Lake
on PATH, set `FTP_EVAL_LEAN_PROJECT` to a built Std-capable project and run:

```bash
FTP_EVAL_LEAN_PROJECT=/path/to/project pytest tests/checking/test_task_types.py -k real_lean
```

`FTP_EVAL_LAKE` can select an explicit Lake executable. This compiles all ten
published task-type answers: six valid proofs/witnesses and four refusals.
For a trusted Linux supervisor with Docker, use an immutable organizer image:

```bash
FTP_EVAL_DOCKER_IMAGE=sha256:IMAGE_ID pytest tests/checking/test_task_types.py -k real_docker
```

That test checks a dependent function through strict environment preflight,
fresh kernel replay and axiom auditing, and rejects a wrong proof at the kernel.
It derives pins from the image only for the integration test; official evaluation
must declare its expected pins independently. Both tests skip when their
environment variable is absent.

- `checking/`: frozen interface, helpers, imports, syntax screening and Goal/Check generation.
- `running/`: input groups, policies, environment pins, metrics, health probes,
  storage contracts, partial runs and durable checkpoints.
- `backends/`: Lean diagnostics, ordered builds, cache identity, replay failure
  paths, Docker isolation, safe artifact transfer, source screens and HTTP mapping.
- `analysis/`: tactics, structure, statement complexity, outcome classification
  and performance guarantees.
- `autoformalization/`: statement faithfulness, judge responses and consensus.
- `test_cli.py`: command behavior, diagnostics, audit output and default run records.
- `test_layering.py`: AST import boundaries and implemented benchmark/artifact contracts.

Place regressions beside the behavior they protect. There is no catch-all
regression file spanning submission rules, environment checks and worker execution.

Tests mirror the leaf package name and omit the intermediate `proving/` layer:
`src/ftp_eval/proving/checking/` maps to `tests/checking/`, `proving/running/`
to `tests/running/`, and `proving/analysis/` to `tests/analysis/`. The top-level
`backends/` and `autoformalization/` packages map directly to their test folders.
The one-file `tests/autoformalization/` directory follows that same rule.

The speed script measures Python analysis and grading overhead with the mock
backend. It does not measure real Lean module-build throughput.
