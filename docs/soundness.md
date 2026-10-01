# Soundness policy

Frozen benchmark grading checks a compiled `Problem.Target` through `Submission.solution` and audits its axiom closure. It does not compare theorem text. The statement-screening sections below describe the independent low-level verifier API. See [architecture](../ARCHITECTURE.md).

## Threat model

### What is assumed rather than checked

The Lean kernel and the pinned toolchain; the organizer's problem modules and
benchmark manifest; the container runtime under `lean4-docker`, and the host
operating system. Nothing below establishes immunity to a kernel bug, a
compiler bug or a container escape. Everything else is checked.

### The rule the rest of this follows

**No verdict rests on reading the answer's source text.** The source screen is
a cheap early layer that gives a participant a clear message; it is not a
boundary, because the text a regex reads and the text Lean compiles can be made
to differ. Verified against Lean 4.29, each of these compiles and runs while a
scanner that does not know the lexical form deletes it from the screened text:

| the form | why a scanner misses it |
|---|---|
| `def «/-» := 0` … `def «-/» := 1` | `«…»` holds arbitrary characters, so these are two declarations and not a comment |
| `def c : Char := '"'` | one character, not the start of a string literal |
| `r#"a "/- b"#` | a raw string ends at a quote followed by as many hashes as it opened |
| `"abc \`<br>`  /- def"` | a backslash before the newline continues the literal |
| `/- note -/ import Lean` | an import that does not begin its own line |

All five are handled now, and `tests/backends/test_comments.py` keeps them
handled. The point is not that the list is complete -- it is that it cannot be
*known* to be complete, so every rule that decides a verdict is enforced
somewhere the answer's text cannot reach:

| rule | enforced by | if the text screen is fooled |
|---|---|---|
| no `sorry`, no placeholder | `#print axioms` closure | `sorryAx` in the listing; refused |
| no fresh `axiom` | `#print axioms` closure | the axiom is in the listing; refused |
| no `native_decide` | `#print axioms` closure | `Lean.ofReduceBool` in the listing; refused |
| the proof inhabits the frozen target | the generated check module's type ascription, kernel-checked | the kernel compares; refused |
| no unchecked declarations (`debug.skipKernelTC`) | `leanchecker --fresh` replay | **only** replay; mandatory on `lean4-docker`, off by default natively |
| the frozen target is the one that was frozen | per-artifact SHA-256, re-read before every later compile | refused as tampering |
| no compile-time execution (`#eval`, `initialize`, `elab`) | the sandbox | contained on `lean4-docker`; **detected, not prevented**, natively |
| imports | `lean --deps`, Lean's own parser | the observed list is the one enforced; refused |

Imports are read back from Lean rather than from the answer's `import` lines.
Before each untrusted module compiles, `lean --deps` reports what it actually
imports; the olean paths are named against the search path Lean itself prints,
the implicit prelude is subtracted by differencing an empty file, and a path
under no known root is marked rather than guessed at, so a module the grader
cannot name is one the allowlist cannot clear. The allowlist rule is
`import_violations`, written once and applied to both the text and the
observation. A backend that cannot answer records `imports_verified: false` on
the answer, which is not the same as a pass.

### Guarantees by backend

| | `mock` | `lean4` | `lean4-docker` |
|---|---|---|---|
| `sandbox` | `none` | `none` | `read-only-linux-container` |
| `safe` | `none` | `none` | `adversarial` |
| kernel check | no | yes | yes |
| axiom audit | no | yes | yes |
| kernel replay | no | opt-in (`require_replay`) | **mandatory** |
| artifact digests | n/a | yes | yes |
| imports read from Lean | no | yes | yes |
| answer cannot reach the grader | n/a | **no** | yes |

Both values are recorded in every run's `environment`, so a score always
carries what it is worth.

`sandbox = "none"` means the answer's Lean process runs as the grading user,
with that user's filesystem and network. Per-answer build directories keep two
answers apart; they do not keep an answer away from the grader. An answer that
executes code while it elaborates can edit this package, the toolchain or the
run records, and no check inside the package can see that. The digest checks
below cover the staged modules specifically, because that is the attack that
produces a *wrong verdict* rather than a broken one -- but detection is not
containment. **Grade answers you did not write under `lean4-docker`.**

### Why artifact digests, and why replay is not a substitute

Lean imports an `.olean` without rechecking it against the source it was built
from. An answer that can write to disk can therefore compile its own
`Problem.lean` with `Target := True` and swap the file between its own compile
and the generated check: the kernel then agrees, and `#print axioms` comes back
clean. Kernel replay does not help, because the forged module is internally
consistent -- it is simply a different problem. The same write, aimed at the
build cache instead, persists across answers and across runs, with the
poisoner's own record showing an ordinary failure and the beneficiary's showing
an honest proof.

So the grader records the SHA-256 of every artifact it puts on the answer's
search path and re-reads them before anything else compiles against them, and
before reading the dependency listing. Cached artifacts carry a digest beside
them; a mismatch is a miss, not an error, so the module is recompiled from its
trusted source and the entry repairs itself. An `.olean` the grader did not
write is refused outright: the staged directory comes first on `LEAN_PATH`, so
a module dropped there shadows the library module of the same name.

### Known residual risks

* `safe = "none"` backends: everything above about the grader's own files.
* A Lean kernel or elaborator bug; a `leanchecker` that shares the bug.
* `debug.skipKernelTC`, whose only backstop is replay. Mandatory under
  `lean4-docker`; off by default natively, where a `leanchecker --fresh` run
  costs about a minute even on a bare `Init` project. Exploiting it needs a
  term the elaborator accepts and the kernel rejects, which would be a Lean
  bug rather than a harness one -- but the harness should not be betting that
  there is none.
* A problem whose `Target` is vacuous or does not mean what its prose says.
  That is a formalization defect, not a grading one; `ftp-eval audit` probes
  for it and faithfulness needs a `prose:` line.
* Resource exhaustion by the answer, bounded by heartbeats, memory and
  timeouts, and by the container's own limits.

## Frozen benchmark grading

The problem module is trusted and compiled first. When gold values are supplied,
a trusted Goal module elaborates those expressions before importing the answer.
The generated check ascribes `Submission.solution` to that frozen constant, then
audits the dependency closure. Open value problems without gold record only the
answer's claimed value and do not establish equality to an organizer answer.
The default axiom allowlist is `propext`, `Classical.choice` and `Quot.sound`.
A missing listing is an audit failure. Interface screening enforces namespace
policy and rejects obvious placeholders, fresh axioms and kernel escapes; the
import allowlist is enforced against what Lean reports rather than against the
screen, as the threat model above describes.
Answers must not add notation, infix/prefix/postfix declarations, syntax categories,
macros, elaborators or `run_tac`. Attribute commands may target only Submission
declarations. Local notation is also refused. `Lean` is excluded from the default
direct import allowlist; organizer dependencies may still import it transitively.
The stdout axiom parser requires an exact declaration name and exactly one
matching listing; missing or duplicate listings fail closed.
See [benchmark policy](../benchmarks/README.md), [run records](runs.md) and
[container workers](container-workers.md).

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

A vacuous value predicate is a problem-design defect: every candidate satisfies
it. Audit the problem folder before publication:

```bash
ftp-eval audit --problems benchmarks/my-2026/problems -b lean4
```

That runs the elaboration, triviality and vacuity probes over every
problem. Cheap proofs are difficulty observations, not evidence of a lost
formalization. For a construction task, checking supplied gold measures
verification cost, not the effort of finding an answer. Universal acceptance
of all values remains a fatal health failure. Faithfulness is judged only for problems that record a `prose:`
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
`reward hacking seen`, excluding placeholder-only refusals. Bare and partial
`sorry` proofs, `admit`, helper placeholders and audited `sorryAx` all remain
unsolved and are classified as incomplete proofs. This classification does not
infer intent. A new axiom or kernel escape is recorded separately even in an
incomplete answer. The lower-level verifier still reports its raw soundness
violations, including placeholders. The report names each remaining pattern, and
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
