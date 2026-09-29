# Development utilities

Run from the repository root after installing the package with `pip install -e ".[dev]"`:

```bash
python scripts/measure_speed.py
python scripts/measure_speed.py --json
python scripts/measure_speed.py --check
```

This measures Python analysis and grading overhead. It does not measure real
Lean compilation or call a model API. Complexity regression tests remain in
`tests/test_performance.py`; CI runs both checks.
