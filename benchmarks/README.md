# Benchmark format

```text
demo-2026/
  benchmark.json
  problems/P001.lean              namespace Problem; abbrev Target ...
  submissions/alice/P001.lean     imports FtpEvalBench.P001; exports Submission.solution
  submissions/bob/P001.lean
```

Three fixtures ship with the package, each covering a different axis:
[demo-2026/](demo-2026/) is the Mathlib quickstart with mixed outcomes,
[task-types/](task-types/) is Std-only and covers the task *shapes*, and
[interface-faults/](interface-faults/) carries one answer per refusal reason.

## What a participant submits

**One `.lean` file per problem, named after the problem id.** Nothing else.

```text
submissions/<your-name>/P001.lean
```

```lean
import FtpEvalBench.P001          -- the problem module. Always allowed.

namespace Submission

-- Anything you need, in any shape, with no correspondence to the problem.
def value : Nat := 38
theorem residues : value % 7 = 3 ∧ value % 11 = 5 := by decide

-- The one hard requirement: this name, this type.
theorem solution : Problem.Target value := residues

end Submission
```

The requirement is a single exported declaration:

```lean
Submission.solution : Problem.Target <your value>
```

For a problem with no value to find, the type is just `Problem.Target`.

You submit neither the organizer's problem module nor the generated Goal and
Check modules. Generated sources are saved in the run artifacts for inspection.

### What you may do

* declare as many `def`s, `lemma`s, `theorem`s and `instance`s as you like,
  inside `namespace Submission`
* use the helper lemmas the problem shipped, or ignore them and prove your own
* import anything on the benchmark's `allowed_imports` list

### What is refused

| | why |
|---|---|
a `sorry` or `admit` anywhere in the file | nothing was proved |
declaring an `axiom`, `opaque` or `constant` | an assumption is not a proof |
`native_decide`, `unsafe`, `partial def`, `@[implemented_by]`, `@[extern]` | these ask the kernel to skip the check |
declaring anything outside `namespace Submission` | it could collide with another answer |
declaring into `namespace Problem` | that module is sealed |
`notation`, `macro`, `macro_rules`, `syntax`, `elab` | an answer that extends syntax can change how the grader's own files parse |
`attribute` on an imported declaration | same reason |
an import the benchmark does not list | a self-supplied module can carry its own axioms |
`Submission.solution` taking binders before its `:` | the check needs a closed proposition |

Everything in that table is reported by name before any prover runs, so a
refusal tells you which rule to fix.

A `sorry` left in `solution` itself is reported as an honest non-answer
rather than as a violation.


## Problem format, for the organizer

A file's stem is its problem id. Each problem module defines `Problem.Target`.
Problem modules are compiled independently, so that namespace can be reused for
different problems. Answers may add arbitrary helpers inside `Submission`.

Declare the target as a `def` or `abbrev` with an explicit result type `: Prop`.
We recommend `abbrev`: it is reducible, so tactics can inspect the proposition
more easily. Both forms elaborate in the sealed problem module and preserve the
same frozen-target boundary.

Use one interface for all three task shapes:

- **Proof:** `abbrev Target : Prop := ...`. Any inputs and hypotheses belong
  inside the proposition, for example `∀ t, mirror (mirror t) = t`.
- **Construction:** declare an optional `Answer` type, then
  `abbrev Target (x : Answer) : Prop := ...`. `Answer` can be a matrix, graph,
  function, predicate or infinite sequence.
- **Parameterized construction:** make `Answer` the whole function. Target
  states that it meets the requirement for every input.

For example, bounded Bézout certificates for consecutive odd integers:

```lean
namespace Problem
abbrev Input := Nat
abbrev Output (n : Input) := Fin (4 * n + 6) × Fin (4 * n + 6)
abbrev Answer := (n : Input) → Output n
abbrev Target (f : Answer) : Prop :=
  ∀ n, (f n).1.val * (2 * n + 3) = (f n).2.val * (2 * n + 1) + 1
end Problem
```

The submission may define `answer (n : Problem.Input) : Problem.Output n`,
but its final theorem is closed: `solution : Problem.Target answer`.
Putting `(n : Problem.Input)` before `solution`'s colon is refused.
Only `Problem.Target` and `Submission.solution` are required names. `Answer`,
`Input`, `Output`, `Valid` and `Submission.answer` are optional helpers.
Declare Target's parameters as binders followed by `: Prop`; an arrow-typed
declaration such as `Target : Answer → Prop` is not the loader's source format.

For an existing `def Target`, tactics such as `omega` may leave the goal hidden
behind the definition. Unfold it first, or use `simp [Problem.Target]`.
For a target `def Target (n : Nat) : Prop := n % 7 = 3 ∧ n % 11 = 5`:

```lean
namespace Submission
theorem solution : Problem.Target 38 := by
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
    "P001": {"module": "FtpEvalBench.P001", "gold_arguments": ["38"]}
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

Omit gold when the task accepts any valid construction. Multiple or infinitely
many correct witnesses need no extra protocol, and `against_gold=false` still
means the witness passed the frozen predicate, kernel check and axiom audit.
Gold selects a particular instantiated proposition; it does not implement an
extensional equality test for arbitrary functions or sets.

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

## Mathematical examples

[task-types/](task-types/) contains four Std-only problems, tested with
Lean 4.29.0-rc2, using the same loader and evaluation pipeline:

- [P001: binary trees](task-types/problems/P001.lean). Prove mirror is an
  involution and the leaf count equals the internal-node count plus one.
  This is a proof task with two induction arguments.
- [P002: a normal 3×3 magic square](task-types/problems/P002.lean). Construct a
  matrix using each number 1–9 exactly once, with every row, column and both
  diagonals summing to 15. Two different orientations are accepted.
- [P003: bounded Bézout certificates](task-types/problems/P003.lean). For every
  `n`, construct coefficients `a,b < 4n+6` such that
  `a(2n+3) = b(2n+1)+1`. Two different dependent functions are accepted.
- [P004: the complete Chinese-remainder solution set](task-types/problems/P004.lean).
  Submit a predicate describing all natural numbers with residues 3 modulo 7
  and 5 modulo 11, and prove equivalence in both directions. The reference
  answer describes the infinite progression `38 + 77k`.

`submissions/standard/` contains four reference proofs, `alternate/` contains
two different valid witnesses, and `invalid/` contains four rejection cases.
These examples exercise the interface; they do not constitute a calibrated
model leaderboard or require a canonical answer.

```bash
ftp-eval grade --problems benchmarks/task-types/problems \
  --submissions benchmarks/task-types/submissions \
  -b lean4 -o project_dir=/path/to/built-lean-project --out runs
```

The example manifest deliberately leaves production toolchain pins to the
organizer. Declare the exact Lean/dependency pins (and immutable image digest
for Docker) before an official run; see [Docker workers](../docs/container-workers.md).

## Every refusal reason, once

[interface-faults/](interface-faults/) is the third fixture and the only one
that is about the grader rather than about mathematics. One deliberately
trivial Std-only Target, and one answer per reason grading can refuse an
answer for:

| participant | refused for |
|---|---|
| [solution-missing](interface-faults/submissions/solution-missing/P001.lean) | helpers, but the one declaration that is the interface was never exported |
| [problem-namespace](interface-faults/submissions/problem-namespace/P001.lean) | declaring into the sealed `namespace Problem` |
| [outside-namespace](interface-faults/submissions/outside-namespace/P001.lean) | a top-level declaration, which can collide with another answer's |
| [import-not-allowed](interface-faults/submissions/import-not-allowed/P001.lean) | `import Lean`, which is off the allowlist |
| [unparsed](interface-faults/submissions/unparsed/P001.lean) | output that stopped mid-declaration, as a truncated generation does |
| [global-instance](interface-faults/submissions/global-instance/P001.lean) | a file-level `instance`, which this manifest forbids |
| [syntax-extension](interface-faults/submissions/syntax-extension/P001.lean) | `notation`, which extends the language the grader reads |
| [imported-attribute](interface-faults/submissions/imported-attribute/P001.lean) | an attribute aimed at a declaration the answer does not own |
| [compile-time-eval](interface-faults/submissions/compile-time-eval/P001.lean) | `#eval`, which runs `IO` while the file elaborates |

`solved/` holds the one answer that presents the interface correctly, so the
fixture cannot pass by refusing everything. No prover is involved: every
refusal here is decided from the text, which is why the Target only has to be
real enough to cite. `global-instance` is also the only end-to-end evidence
that a manifest's `allow_global_instances: false` is really applied --
instances are allowed by default, because the frozen target makes them
harmless.

Each reason also has a unit test beside the rule that raises it. What those
cannot show is that the reason survives into the graded answer, into
`interface/refused.jsonl` and into the printed counts, which is what
[tests/running/test_interface_faults.py](../tests/running/test_interface_faults.py)
pins. `solution_unproved` and `incomplete_proof` are deliberately not here:
`demo-2026` carries those as honest non-answers, where they belong beside a
real problem.

```bash
ftp-eval grade --problems benchmarks/interface-faults/problems \
  --submissions benchmarks/interface-faults/submissions --out runs
```

## MathDB integration

Store each record's prose and provenance together with its frozen Problem
source. Export it under a stable problem id and module name. Choose the type of
Target's argument from the task's mathematical object; a function synthesis task
passes the entire function, and an all-solutions task passes a predicate. Do not
extract or compare witness strings to decide correctness.

Supply answers through `Submission` and call `evaluate_benchmark` with a
`ProblemSet`, or implement the public `Benchmark` contract over MathDB records.
Attach MathDB ids in problem metadata and preserve the run id, source hashes,
environment pins, stage events and final verdicts when importing results.
The evaluator checks supplied Lean answers; model API calls remain the caller's
responsibility. See [architecture](../ARCHITECTURE.md) and
[run records](../docs/runs.md) for those contracts and saved fields.
