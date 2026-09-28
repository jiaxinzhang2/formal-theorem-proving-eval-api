# Soundness screening

A formal prover is only a trustworthy reward signal if the thing that got
proved is the thing that was asked. Several ways of failing that test end
with the prover exiting 0, which is why screening is a layer above every
backend rather than something each backend does for itself.

`Verifier.verify` runs the screen on the assembled source and downgrades
`VERIFIED` to `REJECTED` when it fires. A backend cannot opt out; only
the caller can, with `create(..., check_soundness=False)`, which exists
for debugging and requires saying so explicitly.

## What is checked

| check | the trick it blocks |
|---|---|
| placeholders | `sorry`, `sorryAx`, `admit` (Lean), `Admitted`, `give_up` (Coq), `oops` (Isabelle) |
| new axioms | `axiom cheat : <the goal>` then `exact cheat` — assuming the goal instead of proving it |
| disabled checks | `set_option maxHeartbeats 0`, `debug.skipKernelTC true`, `Unset Guard Checking` |
| `native_decide` | trusts the compiler rather than the Lean kernel, so the result is not kernel-checked |
| statement tampering | proving something weaker, or a different theorem under the required name |

Comments are stripped first, in both directions. `-- TODO: remove the
sorry` must not fail an honest proof, and `/- clean -/ sorry` must not
pass a dishonest one.

## Why `sorry` needs its own check

Lean 4 reports an incomplete proof as a **warning**:

```
Main.lean:3:0: warning: declaration uses 'sorry'
```

The file compiles. The process exits 0. A harness that trusts the exit
code gives a model that answers `sorry` to everything a perfect score.
The Lean backend therefore also inspects the prover's own warnings
(`Lean4Verifier.extra_soundness_checks`), which is stronger evidence than
the regex screen and catches `sorry` reaching the declaration through a
lemma the regex never saw.

## Statement tampering

Only checked when the model supplied the statement — `assembly` of
`full_file` or `header_plus_proof`. The required `formal_statement` must
appear in the source after normalization: comments stripped, whitespace
collapsed, and the trailing `:=`/`:= by` seam cut, since that is where the
proof begins rather than part of the claim.

Under `continue_statement` the harness concatenates the statement itself,
so tampering is impossible and the check is skipped rather than wasted.

This is textual matching, with the limits that implies. A semantically
equivalent reformulation gets flagged; a definitionally sneaky one might
slip through. Flagging an honest reformulation is the better failure
direction — a `rejected` result is visible in the report and can be
reviewed, whereas a silent false `verified` is not.

## Reading the report

```python
result.status                  # Status.REJECTED
result.error_kind              # ErrorKind.SOUNDNESS
result.soundness.ok            # False
result.soundness.violations    # ("proof contains placeholder 'sorry'",)
```

In aggregate, `Summary.soundness_violations` counts each distinct reason,
`Summary.integrity_ok` goes false, and `ftp-eval verify --strict` exits 5.
Rejected attempts count as failures in `pass@k` — they were scoreable and
they did not succeed — so an unsound pass lowers the score rather than
vanishing from the denominator.

## Adding a check

Syntactic patterns go in the tables at the top of
`src/ftp_eval/verifier.py`, keyed by language. Anything that needs to ask
the prover goes in that backend's `extra_soundness_checks`.

Every check needs a test in `tests/test_soundness.py` covering both
directions: the trick is caught, and the honest proof that superficially
resembles it is not.
