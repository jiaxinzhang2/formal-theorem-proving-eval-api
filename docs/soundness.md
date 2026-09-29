# Soundness policy

Frozen benchmark grading checks a compiled `Problem.Target` through `Submission.solution` and audits its axiom closure. It does not compare theorem text. The statement-screening sections below describe the independent low-level verifier API. See [architecture](../ARCHITECTURE.md).

## Frozen benchmark grading

The problem module is trusted and compiled first. When gold values are supplied,
a trusted Goal module elaborates those expressions before importing the answer.
The generated check ascribes `Submission.solution` to that frozen constant, then
audits the dependency closure. Open value problems without gold record only the
answer's claimed value and do not establish equality to an organizer answer.
The default axiom allowlist is `propext`, `Classical.choice` and `Quot.sound`.
A missing listing is an audit failure. Interface screening enforces imports and
namespace policy and rejects obvious placeholders, fresh axioms and kernel escapes.
Answers must not add notation, infix/prefix/postfix declarations, syntax categories,
macros, elaborators or `run_tac`. Attribute commands may target only Submission
declarations. Local notation is also refused. `Lean` is excluded from the default
direct import allowlist; organizer dependencies may still import it transitively.
The stdout axiom parser requires an exact declaration name and exactly one
matching listing; missing or duplicate listings fail closed.
See [benchmark policy](../benchmarks/README.md) and [run records](runs.md).

The source screen is conservative and syntactic; it is not a complete Lean
parser. Verification trusts the selected Lean toolchain, allowed imports and
benchmark environment. Native execution provides process/build isolation. Docker
execution adds read-only OS isolation and fresh built-in kernel replay. See
[container workers](container-workers.md) for its boundaries; these checks do not
establish immunity to every compiler or kernel vulnerability.

## Lower-level verifier screening

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

## When the statement is given

Under `continue_statement` the harness concatenates the statement and the
model writes only the proof body. Two checks — statement tampering and
definition shadowing — are switched off there, and that is not a gap:

The statement's binders and goal are elaborated **in the same command** as
the proof body, so nothing the model writes can alter them. Declarations
it appends land *after* the theorem and cannot retroactively change what
was proved. Running the checks anyway would only produce false flags on
honest proofs that mention the statement's own names.

Everything that lives *inside* the proof stays fully reachable and is
still caught. Verified, not assumed —
`test_in_body_hacks_are_still_caught_when_the_statement_is_given` runs each
vector as a proof continuation:

| reachable in the proof body | caught by |
|---|---|
`sorry`, including inside a `have` | `lean.sorry` |
`sorryAx` cited directly | `lean.sorry_ax` |
`admit` | `lean.admit` |
`native_decide` | `lean.native_decide` |
`set_option maxHeartbeats 0 in tac` | `lean.heartbeats_off` |
`set_option autoImplicit true in tac` | `lean.auto_implicit` |
`set_option debug.skipKernelTC true in tac` | `lean.skip_kernel_tc` |
`Lean.ofReduceBool` as a term | `lean.trust_compiler` |
homoglyph identifiers | `generic.homoglyph_identifier` |
`axiom` / `#exit` / `variable` / `run_cmd` appended after the theorem | flagged anyway, though they cannot change the verdict |

What *does* change is whose problem the statement is. A vacuous or
trivially-true statement in a published problem set is **the setter's bug,
not the participant's**, so audit the problem folder once rather than
per answer:

```bash
ftp-eval audit --problems benchmarks/my-2026/problems -b lean4
```

That runs the elaboration, triviality and vacuity probes over every
problem. Faithfulness is judged only for problems that record a `prose:`
line, and problems without one are reported as unjudged rather than passed:
with no prose, nothing can say the formalization means the right thing, and
the report says so instead of implying the structural pass was enough.

### A statement is not screened for what a proof is screened for

`screen_source` takes a `classes` argument, and the two callers pass
different sets. Frozen-target answers get `ANSWER_HACK_CLASSES`: placeholders, fresh axioms,
kernel escapes, resource uncapping, elaboration tricks and lookalike identifiers.
Statement tampering and definition shadowing are excluded because the target is
already compiled. The lower-level proof API retains `PROOF_HACK_CLASSES`.
A problem statement gets `STATEMENT_HACK_CLASSES`, which deliberately
leaves out:

| left out for statements | why |
|---|---|
`placeholder` | a problem file's `sorry` **is** the hole a participant fills |
`statement_tampering` | its `variable` pattern fires on `variable (n : Nat)`, ordinary Lean in a problem file |
`resource_uncap` | the heartbeat cap is about proof search, not about what the statement means |

This is not a harmless superset in the other direction: screening a
statement with the proof-side rules reports every well-formed problem as
malformed. It did, for all three demo problems, until the split existed.

### Statement tampering, when the model does supply the statement

Checked under `full_file` and `header_plus_proof`. The required
`formal_statement` must appear in the source after normalization: comments
stripped, whitespace collapsed, and the trailing `:=`/`:= by` seam cut,
since that is where the proof begins rather than part of the claim.

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
result.soundness.violations    # ("[placeholder:lean.sorry] proof contains `sorry`, …",)
```

In aggregate, the contest report counts each distinct reason under
`reward hacking seen`, names the specific pattern that fired, and
`ftp-eval grade --strict` exits 5. A refused answer counts as not solved —
it was graded and it did not succeed — so an unsound pass lowers the score
rather than vanishing from the denominator.

## Adding a check

Syntactic patterns go in the `PATTERNS` table in
`src/ftp_eval/backends/soundness.py`, keyed by language. Each entry carries
`requires` (cheap literal triggers checked before the regex) and `example`
(a snippet the pattern must catch, which makes the table self-testing), and
a `HackClass` — which also decides whether it applies to statements as well
as proofs.

Anything that needs to ask the prover goes in that backend's
`extra_soundness_checks`, in `src/ftp_eval/backends/`.

Every check needs a test in `tests/test_soundness.py` covering both
directions: the trick is caught, and the honest proof that superficially
resembles it is not.
