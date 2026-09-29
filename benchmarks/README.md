# Benchmark format

```text
demo-2026/
  benchmark.json
  problems/P001.lean              namespace Problem; def Target ...
  submissions/alice/P001.lean     imports Bench.P001; exports Submission.solution
  submissions/bob/P001.lean
```

A file's stem is its problem id. Each problem module defines `Problem.Target`.
Problem modules are compiled independently, so that namespace can be reused for
different problems. Answers may add arbitrary helpers inside `Submission`.

Module names default to `Bench.<problem-id>`. Pin custom module names, gold values
and policy explicitly in `benchmark.json`:

```json
{
  "name": "demo",
  "version": "2.0.0",
  "problems": {
    "P001": {"module": "Bench.P001", "gold_arguments": ["1"]}
  },
  "policy": {
    "allowed_imports": ["Mathlib", "Std", "Batteries", "Init", "Lean", "Aesop"],
    "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
    "allow_global_instances": true,
    "isolate_builds": true
  },
  "toolchain": {"lean": "leanprover/lean4:v4.15.0", "mathlib_rev": "PIN-A-REAL-REVISION"}
}
```

Gold arguments are Lean expressions. The generated check demands the gold type;
the kernel decides definitional equality, without comparing argument strings.
Their count must match Target's explicit parameter count. Without gold arguments,
a value problem checks the answer's claimed value and records `against_gold=false`.

The exact problem module import is always allowed. Other allowed names are module
prefixes. No answer may import another submission or reopen `Problem`.
Instances are allowed by default; setting `allow_global_instances=false` refuses
instance declarations. Non-isolated builds are unsupported and rejected.

Only successful kernel and axiom checks count as solved. Missing answers are
distinct from wrong answers. Misfiled files are reported separately.

```bash
ftp-eval grade --problems benchmarks/demo-2026/problems \
  --submissions benchmarks/demo-2026/submissions --out runs
```

This dry run saves every stage but counts zero solved because no prover ran.
See [run lifecycle](../docs/runs.md) for output paths and interrupted runs.
