# formal-theorem-proving-eval-api

A unified verifier API for evaluating formal theorem-proving models.

You write your evaluation once against one interface. Underneath, the
proof gets checked by Lean 4, by a remote verification service, or by a
fake prover in CI — and your scoring code does not change.

```python
from ftp_eval import ProofTask, ProofAttempt, create

verifier = create("lean4", project_dir="~/my-mathlib-project")

task = ProofTask(
    task_id="nat_add_zero",
    header="import Mathlib",
    formal_statement="theorem nat_add_zero (n : Nat) : n + 0 = n := by",
)
result = verifier.verify(task, ProofAttempt(task_id="nat_add_zero", proof=" simp"))

print(result.status)       # Status.VERIFIED
print(result.error_kind)   # None
print(result.soundness.ok) # True
```

Zero runtime dependencies — the core is standard library only, so a
verification run cannot break because of an unrelated dependency bump.

## Why this exists

Compiling a proof is the easy part. Turning a pile of compiler exit codes
into a number you would put in a paper is where evaluations go wrong, and
they go wrong in three specific ways that this package is built around.

**A prover's exit code is not a verdict.** `lake env lean` exits 0 on a
file full of `sorry`; Lean reports it as a *warning*. A model that answers
every problem with `sorry` scores 100% on a naive harness. So every
accepted proof is screened for the known ways of gaming a prover —
placeholders, freshly declared axioms, disabled kernel checks,
`native_decide`, and a mutated theorem statement — and one that trips the
screen comes back `REJECTED`, never `VERIFIED`. See
[docs/soundness.md](docs/soundness.md).

**A broken toolchain is not a weak model.** When the prover is
misconfigured, a harness that scores those attempts as failures reports a
bad model instead of a bad setup. Here, attempts that never got a verdict
(`ERROR`, `SKIPPED`) are excluded from the pass-rate denominator and
reported on their own line, `Summary.integrity_ok` goes false, and
`--strict` exits non-zero.

**`pass@k` is not "did any of my k samples pass".** With *n* samples and
*k* < *n* the naive version is biased upward. This uses the unbiased
estimator from Chen et al. (2021), and refuses to report `pass@10` from
5 samples rather than quietly approximating it.

## Install

```bash
git clone https://github.com/jiaxinzhang2/formal-theorem-proving-eval-api
cd formal-theorem-proving-eval-api
pip install -e ".[dev]"
pytest
```

Requires Python 3.10+. The `lean4` backend additionally needs a built
Lean 4 project; nothing else does.

## The CLI

```bash
# What can run on this machine?
ftp-eval backends

# Try the whole pipeline with no prover installed.
ftp-eval verify -b mock \
  --tasks examples/tasks.jsonl \
  --attempts examples/attempts.jsonl \
  --out results.jsonl --k 1,2
```

```
[1/8] OK    nat_add_zero                    0.0s
[4/8] fail  add_comm_two                    0.0s  [unsolved_goals]
[7/8] CHEAT imo_style_hard                  0.0s  <- proof contains placeholder 'sorry'

backend=mock  model=demo-model-a
tasks=4  attempts=8  scoreable=8
verified=4  failed=3  timeout=0  rejected=1  error=0  skipped=0
solve rate (>=1 sample) = 75.0% (3/4)
pass@1   = 50.0%
pass@2   = 75.0%
error kinds: tactic_failed=2, unsolved_goals=1, soundness=1
SOUNDNESS violations (not counted as passes):
     1 x proof contains placeholder 'sorry'
by split:
  test         solve= 50.0%  verified=1/4
  valid        solve=100.0%  verified=3/4
```

That seventh line is the point of the package. The mock prover *accepted*
that proof; it still does not count.

| command | what it does |
|---|---|
| `backends` | list backends and whether each can run here |
| `doctor -b NAME` | diagnose one backend; non-zero exit if unusable |
| `verify` | run a task set, streaming results to JSONL |
| `score results.jsonl` | pass@k and breakdowns from a saved run |
| `compare a=x.jsonl b=y.jsonl` | rank several runs side by side |
| `preview` | print the exact source a backend would receive |

Run `preview` once per new dataset. A mis-set `assembly` makes every
attempt a syntax error and looks exactly like a terrible model.

### Long runs

```bash
ftp-eval verify -b lean4 -o project_dir=~/mathlib-project \
  --tasks minif2f.jsonl --attempts samples.jsonl \
  --out results.jsonl --timeout 300 -j 8 \
  --cache-dir .cache --resume --k 1,5,10 --strict
```

* results are flushed to `--out` as each one lands, so you watch progress
  and keep partial work if the run dies;
* `--resume` skips attempts already in the output file;
* `--cache-dir` keys verdicts on (backend, statement, proof text), so
  adding one model to a comparison only pays for the new work, and
  editing a statement invalidates its entry automatically;
* the run aborts after 20 consecutive harness errors instead of spending
  an hour on a misconfigured toolchain;
* `--strict` exits 4 on harness errors and 5 on unsound passes — useful
  as a CI gate.

## Backends

| name | status | needs |
|---|---|---|
| `mock` | complete | nothing; deterministic fake prover for CI and demos |
| `lean4` | complete | a built Lean 4 project (`lake`, `.lake/`, Mathlib) |
| `axle` | plumbing only | endpoint URL + credential, **and a response mapping you supply** |

### lean4

Batch compilation: one `lake env lean` process per attempt, full
elaboration, no persistent session. Slower per attempt than driving a
REPL, but its verdict is the easy one to trust — the file either compiles
against the Mathlib you pinned or it does not, with nothing carried over
from a previous attempt.

```bash
export FTP_EVAL_LEAN_PROJECT=~/mathlib-project   # must be pre-built:
                                                 # lake exe cache get && lake build
ftp-eval doctor -b lean4
```

`doctor` checks for `lake`, the lakefile, and a `.lake/` build directory,
because without the last one every attempt pays for a full Mathlib build.
Lean messages are parsed into positions and classified into a shared
taxonomy (`unknown_identifier`, `unsolved_goals`, `tactic_failed`,
`resource_limit`, …), and the goal state under an "unsolved goals" error
is kept attached to it.

### axle (and any other HTTP verification service)

This one ships as **plumbing, not an integration**. Auth, retries with
backoff, timeouts, error classification and response mapping are all
here; the field names are not, because these APIs are not stable public
standards and guessing them is how a harness ends up reporting `failed`
for proofs that were actually accepted. You supply the mapping from the
provider's own docs:

```python
create("axle",
       url="https://api.example.com/v1/verify",
       request_map={"source": "code", "timeout": "timeout_seconds"},
       response_map={"ok": "proved", "messages": "diagnostics"})
```

An unrecognized response shape raises rather than defaulting to "failed".
Before trusting any numbers from a remote backend, send it one
known-good and one known-broken proof and confirm both verdicts.

### Adding your own

Implement one method. See [docs/adding-a-backend.md](docs/adding-a-backend.md):

```python
from ftp_eval import Verifier, RawVerdict, register

class MyProver(Verifier):
    name, language = "myprover", "mylang"

    def _verify(self, task, attempt, source, timeout_s):
        ok, log = my_prover.check(source, timeout=timeout_s)
        return RawVerdict.verified() if ok else RawVerdict.failed()

register("myprover", MyProver)
```

Source assembly, soundness screening, timing, timeouts, retry semantics
and error wrapping are handled by the base class. Soundness screening in
particular is **not** something a backend can opt out of.

## Library use

```python
from ftp_eval import EvalRunner, RunConfig, create, load_tasks, load_attempts, summarize

runner = EvalRunner(
    create("lean4", project_dir="~/mathlib-project"),
    RunConfig(timeout_s=300, concurrency=8, cache_dir=".cache"),
    on_progress=lambda e: print(e.format_line()),
)

results = runner.run(
    load_tasks("minif2f.jsonl"),
    load_attempts("samples.jsonl"),
    out_path="results.jsonl",
)

summary = summarize(results, ks=(1, 5, 10))
print(summary.pass_at[5], summary.integrity_ok)
```

`iter_run` yields results as they land if you want to drive your own
progress display or push them somewhere else mid-run.

## Data format

Tasks and attempts are JSONL, one object per line (a plain JSON array is
also accepted). Unrecognized columns are preserved in `metadata` rather
than dropped — benchmark files carry provenance worth keeping.

```jsonc
// tasks.jsonl
{"task_id": "nat_add_zero", "language": "lean4", "split": "valid",
 "assembly": "continue_statement", "header": "import Mathlib",
 "formal_statement": "theorem nat_add_zero (n : Nat) : n + 0 = n := by"}

// attempts.jsonl  -- k samples per task, numbered
{"task_id": "nat_add_zero", "sample_index": 0, "model": "my-model", "proof": " simp"}
```

`assembly` says what the model was asked to emit, and is per task because
benchmarks disagree:

| value | meaning |
|---|---|
| `continue_statement` | header + statement + attempt (miniF2F: the model continues `:= by`) |
| `full_file` | the attempt is a complete source file |
| `header_plus_proof` | header + attempt, which restates the theorem |

Under `full_file` and `header_plus_proof` the model supplies the theorem
statement, so it is checked against the required one. Under
`continue_statement` the harness concatenates the statement itself and
tampering is impossible by construction.

## Interpreting a result

| status | meaning |
|---|---|
| `verified` | accepted, and no soundness violation found |
| `rejected` | the prover accepted it, but it does not count — see `soundness.violations` |
| `failed` | the prover ran and did not accept the proof |
| `timeout` | still working when the budget ran out |
| `error` | the prover or harness broke; **excluded from the denominator** |
| `skipped` | never attempted (no sample supplied, backend down, filtered out) |

Two numbers, because they answer different questions:

* **solve rate** — fraction of *tasks* solved by at least one sample.
* **pass@k** — mean unbiased per-task probability that *k* draws contain
  a correct one. Tasks with fewer than *k* scoreable samples are dropped
  rather than padded with failures, which would bias the result downward.

## Limitations

Worth stating plainly, since the whole point is trustworthy numbers:

* **The soundness screen is syntactic.** It catches the known tricks, not
  every possible one. For Lean it is backed up by the prover's own
  `sorry` warning, which is stronger evidence; a `#print axioms` audit
  would be stronger still and is not implemented yet.
* **Statement matching is textual.** A semantically equivalent
  reformulation of the goal will be flagged, and a definitionally sneaky
  one might pass. Under `continue_statement` the question does not arise.
* **The `axle` backend is unverified against a live service.** It is
  plumbing with a configurable mapping, by design.
* **No sandbox.** A verifier backend runs a prover on model-generated
  input; Lean can read files and shell out via `#eval`. Run untrusted
  attempts in a container.
* **`pass@k` assumes your samples are i.i.d.** draws at one temperature.
  If you mixed settings or deduplicated samples, the estimator's
  assumption is broken and the number means less than it looks like.

## License

MIT
