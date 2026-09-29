# Frozen-target example

`frozen-target/` contains the three files in the grading contract:

- `Problem.lean` defines the trusted `Problem.Target` before any answer exists.
- `Answer.lean` imports it, adds helpers and an instance, and exports `Submission.solution`.
- `Check.lean` gives that constant the benchmark's required type and prints its axioms.

`tests/test_interface.py` checks the committed Check source against the generator.
The complete grouped example is [benchmarks/demo-2026](../benchmarks/demo-2026/).
For real verification, use a backend that supports isolated module builds.
The mock backend cannot establish these proofs and reports them unverified.
