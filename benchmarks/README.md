# Benchmark format

```text
demo-2026/
  benchmark.json
  problems/P001.lean              namespace Problem; abbrev Target ...
  submissions/alice/P001.lean     imports FtpEvalBench.P001; exports Submission.solution
  submissions/bob/P001.lean
```

A file's stem is its problem id. Each problem module defines `Problem.Target`.
Problem modules are compiled independently, so that namespace can be reused for
different problems. Answers may add arbitrary helpers inside `Submission`.

Declare the target as a `def` or `abbrev` with an explicit result type `: Prop`.
We recommend `abbrev`: it is reducible, so tactics can inspect the proposition
more easily. Both forms elaborate in the sealed problem module and preserve the
same frozen-target boundary.

```lean
namespace Problem
abbrev Target (n : Nat) : Prop := n = 1
end Problem
```

For an existing `def Target`, tactics such as `omega` may leave the goal hidden
behind the definition. Unfold it first, or use `simp [Problem.Target]`:

```lean
namespace Submission
theorem solution : Problem.Target 1 := by
  unfold Problem.Target
  omega
end Submission
```

This is tactic preparation, not an additional submission requirement: proofs
using definitional equality can also work without an explicit unfolding step.

Module names default to `FtpEvalBench.<problem-id>`. Pin custom module names, gold values
and policy explicitly in `benchmark.json`:

```json
{
  "name": "demo",
  "version": "2.0.0",
  "problems": {
    "P001": {"module": "FtpEvalBench.P001", "gold_arguments": ["1"]}
  },
  "policy": {
    "allowed_imports": ["Mathlib", "Std", "Batteries", "Init", "Aesop"],
    "allowed_axioms": ["propext", "Classical.choice", "Quot.sound"],
    "allow_global_instances": true,
    "isolate_builds": true
  },
  "toolchain": {"lean": "leanprover/lean4:v4.15.0", "mathlib_rev": "PIN-A-REAL-REVISION"}
}
```

Gold arguments are Lean expressions, frozen in a trusted Goal module before
the answer is imported. The generated check demands that goal constant;
the kernel decides definitional equality, without comparing argument strings.
Their count must match Target's explicit parameter count. Without gold arguments,
a value problem freezes `∃ values, Problem.Target values`, infers witnesses from
`Submission.solution`, and records `against_gold=false`. The claimed value is
reported but is not copied back into the check's expression.

The exact problem module import is always allowed. Other allowed names are module
prefixes. No answer may import another submission or reopen `Problem`.
Instances are allowed by default; setting `allow_global_instances=false` refuses
instance declarations. Non-isolated builds are unsupported and rejected.
Answers cannot extend syntax, macros or notation, or change attributes of imported
declarations. Generated module roots cannot collide with project `lean_lib` names
or trusted library roots. Existing answers using the old default `Bench.<id>`
must import `FtpEvalBench.<id>` or explicitly configure a non-conflicting module.

Only successful kernel and axiom checks count as solved. Missing answers are
distinct from wrong answers. Misfiled files are reported separately.

```bash
ftp-eval grade --problems benchmarks/demo-2026/problems \
  --submissions benchmarks/demo-2026/submissions --out runs
```

This dry run saves every stage but counts zero solved because no prover ran.
See [run lifecycle](../docs/runs.md) for output paths and interrupted runs.
