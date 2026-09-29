# Adding a backend

A backend answers one question — *does this proof close this goal?* — in
the shared vocabulary of `ftp_eval.backends.types`. Everything else (source
assembly, soundness screening, timing and error wrapping) is handled by the
base verifier. Each adapter handles its own timeout, isolation and caching.

Read `src/ftp_eval/backends/mock.py` first. It is the reference
implementation and its actual logic is about thirty lines.

## The minimum

```python
from ftp_eval import Verifier, RawVerdict, register
from ftp_eval.backends.types import Diagnostic, ErrorKind, Severity

class MyProver(Verifier):
    name = "myprover"       # what --backend myprover resolves to
    language = "mylang"     # must match the tasks' `language` field
    thread_safe = True      # False forces concurrency=1

    def _verify(self, task, attempt, source, timeout_s):
        ok, log = my_prover.check(source, timeout=timeout_s)
        if ok:
            return RawVerdict.verified({"log": log})
        return RawVerdict.failed(
            ErrorKind.UNSOLVED_GOALS,
            [Diagnostic(Severity.ERROR, log, kind=ErrorKind.UNSOLVED_GOALS)],
            {"log": log},
        )

register("myprover", MyProver)
```

`source` is already assembled per the task's `assembly`; do not rebuild
it. Return a `RawVerdict`, not a `VerificationResult` — the base class
adds timing, soundness and identifiers.

## Rules that matter

**Never invent a verdict.** If you cannot tell what the prover meant,
raise `VerifierError`. It becomes `Status.ERROR`, which is excluded from
the pass-rate denominator and flagged in the report. A guessed `failed`
is silently wrong and will be quoted in someone's paper.

The Lean backend takes this seriously: a non-zero exit with no parseable
diagnostics raises rather than scoring the attempt as a failure, because
that pattern means the toolchain misbehaved.

**Distinguish "cannot run" from "did not prove it."** Missing binary,
bad credential, unreachable service → `BackendUnavailable`
(`ErrorKind.TOOLCHAIN`). Implement `info()` so `ftp-eval doctor` can say
what is wrong before a 500-task run starts:

```python
def info(self):
    if not shutil.which("myprover"):
        return BackendInfo(self.name, self.language, False,
                           detail="myprover not on PATH; install it with ...")
    return BackendInfo(self.name, self.language, True, version=my_version())
```

Make the `detail` say how to fix it. It is the message a user sees at the
moment they are stuck.

**Respect `timeout_s`.** It is a wall-clock budget per attempt, already
resolved against any per-task override. Return `RawVerdict.timeout()`
rather than raising. A backend that ignores it will hang a long run on one
pathological `simp`.

**Classify errors.** Map the prover's messages onto `ErrorKind` — this is
what makes a report diagnosable instead of a pass/fail count. Look at
`_CLASSIFIERS` in `backends/lean4.py`; order matters, since
"unknown identifier" is also a type error and the specific label is the
useful one. Use `ErrorKind.UNKNOWN` when you genuinely do not know, never
a plausible-looking guess.

**Put positions in diagnostics.** `line`/`column` make failures
reviewable at scale.

**Keep the raw payload.** Stash stdout, exit code or HTTP body in
`RawVerdict(raw=...)`. It is what you will need when a result is
surprising, and it round-trips through the results JSONL.

## Soundness

You cannot skip the shared screen, and you should not try to reimplement
it. What you *can* do is add stronger evidence:

```python
def extra_soundness_checks(self, task, attempt, source, verdict):
    report = SoundnessReport()
    if verdict.status is Status.VERIFIED and "uses 'sorry'" in str(verdict.raw):
        report = report.with_violation("prover reports the declaration uses 'sorry'")
    return report
```

Only bother when the prover can be asked directly — an axiom listing
beats any regex. See [soundness.md](soundness.md).

## Thread safety

A caller parallelizing with threads must only use `thread_safe = True` when
concurrent `_verify` calls do not share mutable state. Separate
subprocesses with separate temp files qualify; a single long-lived REPL
session does not. Use `thread_safe = False` to signal that callers must use
serial execution. Separate answer directories are required for module builds.

If you write temp files, write them where the prover can resolve imports
— for Lean that means inside the project directory, not the system temp
dir — and clean them up in a `finally`.

## Registering

For a backend inside this package, add it to `_BUILTINS` in
`registry.py`; it is imported lazily, so a missing toolchain cannot break
the others. For one in your own package, publish an entry point:

```toml
[project.entry-points."ftp_eval.backends"]
myprover = "my_package.backend:MyProver"
```

Constructor kwargs are reachable from the CLI as `-o key=value`, with
values JSON-decoded when possible (`-o timeout=30` gives an int).

## Testing

Test the verdict-mapping logic without the prover installed — that is
where mistakes silently corrupt scores. `tests/test_lean4_parsing.py`
does this for Lean: real log samples in, expected `ErrorKind` out. Cover
at minimum:

* a clean success;
* each error class you classify;
* prover output that means nothing (assert it raises, not that it returns
  `failed`);
* a timeout;
* `info()` when the toolchain is absent.

Then, once, against the real prover: one known-good proof and one
known-broken proof. A backend that says "available" but cannot tell those
apart is worse than one that says it is broken.


## Frozen benchmark grading

Implement `supports_module_builds()` and
`build_modules(modules, audit_declaration=..., timeout_s=..., on_stage=...)` for grading.
The capability probe must compile one module and import it from another; official
preflight refuses a backend that cannot demonstrate this. For Lean, probe the
actual generated roots and nested module shape, and refuse roots that collide
with project libraries. Report compile, replay
and dependency-extraction checkpoints through `on_stage` as work completes.
Compile modules in order, in a fresh answer-specific build directory and fresh
processes. Only trusted modules marked `cacheable` may be reused. Return ModuleBuild
with the kernel status and the requested declaration's axiom closure. A missing
listing is `axioms=None`; a verified empty closure is `axioms=()`.
Include diagnostics and raw backend responses for persistent checkpoints.
Returning None signals unsupported module builds and never counts as solved.
The low-level verify/probe interface alone is insufficient for benchmark grading.
