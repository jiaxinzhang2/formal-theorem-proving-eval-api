# Tests

455 tests, no Lean required — everything runs on the `mock` backend.

```bash
pytest -q                          # all of them
python tests/measure_speed.py  # the speed numbers, reproducible
```

| file | what it pins down |
|---|---|
`test_matching.py` | stage ①: does `answer.lean` state `theorem.lean`? |
`test_matching_adversarial.py` | **attacks** on stage ①. Every case is one that worked when first tried |
`test_grading.py` | the three-stage pipeline, and the results folder |
`test_reward_hacking.py` | every check in `soundness.py`, both directions |
`test_soundness.py` | the screen at the verifier layer |
`test_comments.py` | comment handling — exploitable when it is wrong |
`test_statement_and_judge.py` | statement probes, judges, consensus voting |
`test_lean4_parsing.py` | Lean log parsing and error classification |
`test_modes.py` | failure modes with attribution; success modes |
`test_proof_analysis.py` | tactics, structure, repetition, correlations |
`test_metrics.py` | pass@k, including where the naive version is wrong |
`test_runner.py` | batch execution: streaming, resume, caching |
`test_cli_and_io.py` | the CLI and JSONL round-tripping |
`test_performance.py` | **complexity guarantees** — cost at n vs 4n |
`measure_speed.py` | the measurement script CI runs with `--check` |

## Two files worth reading even if you never run the suite

**`test_matching_adversarial.py`** is the honest account of how much the
grader can be trusted. Each test is an attack, labelled with the direction
it fails in:

* **MISS** — an answer that does not prove the theorem is accepted. A wrong
  name on a leaderboard, and invisible.
* **NOISE** — an honest answer is flagged. Reviewable, so tolerable.

The bar is zero misses; noise is traded for that deliberately. Four attacks
found real misses when the suite was first written — a string literal
containing `--`, a bare name under a different namespace, a duplicate
declaration, and a dependency reached indirectly. All four are fixed and
pinned here.

**`test_performance.py`** guards complexity rather than wall time, by
comparing cost at size *n* against *4n*. That is machine-independent, so it
means the same thing on a fast desktop and a throttled runner. The guard is
verified to fire: restoring the original quadratic dependency walk makes it
report 58.6× growth for a 4× input against a 9× ceiling.
