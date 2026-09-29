# Tests

```bash
pytest
ruff check src tests scripts
mypy
python scripts/measure_metrics_speed.py --check
```

The Python suite does not require Lean. Simulated module-build responses exercise
control flow and persistence; they are not real kernel verification.

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
