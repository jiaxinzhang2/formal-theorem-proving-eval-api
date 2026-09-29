# Development utilities

Run from the repository root after installing the package with `pip install -e ".[dev]"`:

```bash
python scripts/measure_metrics_speed.py
python scripts/measure_metrics_speed.py --json
python scripts/measure_metrics_speed.py --check
```

This measures Python analysis and grading overhead. It does not measure real
Lean compilation or call a model API. Complexity regression tests remain in
`tests/analysis/test_performance.py`; CI runs both checks.

On a Linux supervisor with Docker and a built, digest-pinned organizer image:

```bash
python scripts/smoke_docker.py sha256:IMAGE_DIGEST
```

This runs real Lean compilation, kernel replay, axiom rejection, gold isolation,
problem-health probes, container restrictions, artifact transfer and timeout
cleanup. See [container workers](../docs/container-workers.md) for image setup.

Keep reusable implementation under `src/ftp_eval/`. These scripts are developer
entry points, not modules imported by the library or a second CLI implementation.
