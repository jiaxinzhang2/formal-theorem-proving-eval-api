# ftp_eval

Three dependency layers: spec contracts, prover backends, then the two independent
APIs (proving and autoformalization). Top-level CLI, registry and exports compose them.

Start with `proving/interface.py`: freeze Problem.Target, accept arbitrary helpers,
and check Submission.solution with the kernel and an axiom audit. `proving/grading/contest.py`
applies that contract to a benchmark; `proving/grading/artifacts.py` checkpoints each step.

See [architecture](../../ARCHITECTURE.md) and [run lifecycle](../../docs/runs.md).
`tests/test_layering.py` enforces API independence and downward imports using ASTs.
