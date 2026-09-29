# Tests

```bash
pytest
ruff check src tests scripts
mypy
python scripts/measure_speed.py --check
```

The Python suite does not require Lean. Simulated module-build responses exercise
control flow and persistence; they are not real kernel verification.

- `test_interface.py`: frozen interface, helpers, imports, axiom auditing and Check generation.
- `test_grading.py`: benchmark groups, policies, gold arguments, partial runs and durable checkpoints.
- `test_cli_and_io.py`: CLI, registry, JSONL and HTTP mapping.
- `test_layering.py`: AST import boundaries and implemented benchmark/artifact contracts.
- `test_reward_hacking.py`, `test_soundness.py`, `test_comments.py`: source screening.
- `test_problem_health.py`, `test_statement_and_judge.py`: independent original-statement auditing.
- `test_lean4_parsing.py`: Lean diagnostics; `test_module_builds.py`: isolated build mechanics.
- Analysis, mode and performance tests cover proof metrics and scaling.

The speed script measures Python analysis and grading overhead with the mock
backend. It does not measure real Lean module-build throughput.
