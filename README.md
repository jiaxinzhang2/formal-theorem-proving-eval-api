# formal-theorem-proving-eval-api

A unified API for evaluating formal theorem-proving models.

A dataset of formal theorem proving has three artifacts — a problem in
natural language, a formal statement, a formal proof — and therefore **two
links to check**, not one:

```
natural language  ──①──▶  formal statement  ──②──▶  formal proof
                  faithful?                 valid?
```

Both are here, behind one interface, with pluggable prover backends:

```python
from ftp_eval import StatementTask, ProofAttempt, StatementChecker, create, create_judge

verifier = create("lean4", project_dir="~/mathlib-project")
checker  = StatementChecker(verifier, create_judge("claude"))

task = StatementTask(
    task_id="nat_add_zero",
    informal_statement="Show that n + 0 = n for every natural number n.",
    header="import Mathlib",
    formal_statement="theorem nat_add_zero (n : Nat) : n + 0 = n := by",
)

print(checker.check(task).status)                                    # ① StatementStatus.OK
print(verifier.verify(task.to_proof_task(),
                      ProofAttempt(task_id="nat_add_zero",
                                   proof=" simp")).status)           # ② Status.VERIFIED
```

Zero runtime dependencies — the core is standard library only. The Claude
judge needs `anthropic`; nothing else does.

## Why both links

Checking only the proof has a trivial winning strategy: formalize the
problem as `True` and prove it with `trivial`. Every proof checks out, the
score is 100%, and nothing was proved. The demo below shows that
concretely — a proof-only reading of the same data says 100%, and the
two-link reading says 50% and names the difference:

```
end-to-end: 4 task(s), 2 solved (50.0%)
  proved_wrong_statement          2   valid proof of the WRONG statement
  solved                          2   faithful statement + valid proof
IMPORTANT: 2 task(s) had a valid proof of an unfaithful statement. A
proof-only harness would have scored every one of those as a success.
```

## Passing the kernel is not the bar

The load-bearing idea of this package. A prover's "yes" does not mean a
proof exists:

| what the model writes | what the kernel does |
|---|---|
`sorry` | **Compiles.** Lean reports it as a *warning*; exit code 0 |
`axiom cheat : <the goal>` | **Compiles with no complaint at all.** Asserting a proposition as an axiom is, to the kernel, perfectly well-formed |
`variable (h : False)` | **Compiles.** Lean silently adds it to the theorem as a hypothesis, weakening it to nothing |
`#exit` | **Compiles.** Lean stops reading the file; anything after is never checked |
contradictory hypotheses | **Compiles, and the proof is genuinely valid.** Anything follows from a contradiction, so the theorem says nothing |
`native_decide` | **Compiles.** The result is trusted from the compiler, not checked by the kernel |

So every accepted proof is screened above the backend layer, and a proof
that trips the screen comes back `REJECTED`, never `VERIFIED`. Backends
cannot opt out. For Lean there is a second, stronger layer: a `#print
axioms` audit, which is the only check that sees through indirection — a
`sorry` inside a helper lemma three files away still shows up in the axiom
set, and a declared axiom shows up nowhere else.

Detected, each recorded by class **and** by specific trick:

| class | tricks |
|---|---|
`placeholder` | `sorry`, `sorryAx`, `admit`, `proof_wanted`, Coq `Admitted`, Isabelle `oops` |
`new_axiom` | `axiom`, `constant`, `opaque`, Coq `Parameter`/`Hypothesis`, non-allowlisted `import` |
`kernel_bypass` | `native_decide`, `debug.skipKernelTC`, `@[implemented_by]`, `@[extern]`, `unsafe`, `partial def`, `#exit`, `run_cmd`/`elab` metaprogramming, Coq `Unset Guard Checking` |
`resource_uncap` | `set_option maxHeartbeats 0` |
`statement_tampering` | statement altered or replaced, `variable` hypothesis injection |
`definition_shadowing` | redefining a term the statement uses, `notation`/`macro_rules`, `open` that redirects a name |
`elaboration_trick` | `autoImplicit`, `checkBinderAnnotations false`, low-level `backward.*` options |
`homoglyph` | identifiers mixing ASCII with lookalike Unicode |

Vacuous hypotheses are handled on the *statement* side (`ProbeKind.VACUOUS`),
because there the proof is genuinely valid and only the statement is at
fault.

## Two Lean files: does `answer.lean` prove `theorem.lean`?

The input contract for a formal-conjectures-style setup: a problem file and
a submitted file, one theorem per problem.

```bash
ftp-eval match theorem.lean answer.lean
```

```
target:  demo_least_N_3
verdict: mismatched
text screen: mismatched
answer(s) submitted: 4
definitions: changed=1
  FATAL dependency_changed  abbrev IsSumDistinctSet is defined differently in
                            the answer, so the theorem no longer means what it says
    expected: ... := A ⊆ Finset.Icc 1 N ∧ (fun (⟨S, _⟩ : A.powerset) => S.sum id).Injective
    actual:   ... := True
  The answer may compile perfectly on its own; what it does not do is state
  the theorem that was set.
```

The question is **what the answer proves**, not whether it compiles — the
example above compiles fine. Answered in two stages, and when they disagree
the kernel wins:

| stage | what it does | needs a prover |
|---|---|---|
text screen | signatures compared after normalizing `answer(...)` holes and whitespace; definitions diffed; helpers checked; reward hacking screened | no |
kernel confirmation | states the **theorem's** proposition and closes it with the **answer's** proof term — if that typechecks, the answer proves the theorem whatever the texts look like | yes (`-b lean4`) |

Four properties of the format make the naive answer wrong, and each one is
handled:

**`theorem.lean` introduces definitions.** It is not just a statement: the
`abbrev`s and `def`s the statement is written in terms of are the problem's
vocabulary. An answer that redefines one has changed the problem while
leaving the theorem's text byte-identical. So the theorem file is the
authority on them — including when building the confirmation probe, which
would otherwise compile the answer against its own gutted definitions and
confirm a claim nobody asked for.

**`answer.lean` may introduce many more.** A real proof brings its own
machinery, and none of it is penalised — the report just counts it
(`definitions: added=2, unchanged=1`). What grows with it is the number of
places to hide an unproved assumption, so a helper the target *cites* but
leaves `sorry`-backed is fatal, while an uncited one is reported as dead
weight.

**`sorry` in `theorem.lean` is legitimate.** An open conjecture is *stated*
that way. So a right statement with no proof is `matched_but_unproved` — an
honest miss, not tampering and not cheating.

**`answer(sorry)` is a hole the answer fills.** `= answer(sorry)` becomes
`= answer(4)`, so the statement legitimately changes and verbatim comparison
would flag every solved problem. The holes are normalized away; everything
else — binders, implicitness, bounds, `=` vs `≤` — is compared exactly.

Runnable fixtures in [examples/lean-files/](examples/lean-files/): an honest
answer, one with helper definitions, one that weakens the claim, one that
guts a definition, one that leaves a cited helper unproved, one with no
proof yet.

## Layout

The package is organized along the two links, so the structure shows the
architecture rather than hiding it:

```
ftp_eval/
├── formalizing/     ① is the formalization faithful to the problem?
│   ├── checker.py       structural probes + an LLM judge, kept distinct
│   ├── judge.py         the judge interface, a mock, consensus voting
│   └── judges/          provider-backed judges (claude)
├── proving/         ② does the proof close the goal?
│   ├── verifier.py      the interface every backend implements
│   ├── runner.py        batch execution: streaming, resume, caching
│   └── backends/        mock, lean4, axle
├── analysis/        what happened, for passes and failures alike
│   ├── tactics.py       which tactics, in what order
│   ├── structure.py     proof shape, statement complexity, correlations
│   ├── modes.py         failure modes with attribution; success modes
│   └── scoring.py       unbiased pass@k and the Summary
├── pipeline.py      both links together, with a combined verdict
│
├── types.py         the vocabulary every layer speaks   ─┐
├── soundness.py     reward-hacking detection             │ shared by
├── comments.py      what counts as a comment, per language │ all three
├── dataset.py       JSONL I/O, the three-file layout     │ layers
├── registry.py      backend and judge lookup by name    ─┘
└── cli.py
```

The whole public API is importable straight from `ftp_eval` — these paths
matter only when extending the package. Two names are deliberately
independent of the layout: the `ftp_eval.backends` entry-point group (a
plugin contract third parties write into their own metadata, so it must
not move when this package is reorganized) and every symbol in
`__all__`.

## Install

```bash
git clone https://github.com/jiaxinzhang2/formal-theorem-proving-eval-api
cd formal-theorem-proving-eval-api
pip install -e ".[dev]"        # add ".[dev,judge]" for the Claude judge
pytest
```

Python 3.10+. The `lean4` backend needs a built Lean 4 project; nothing
else does.

## The CLI

```bash
ftp-eval backends          # which provers can run here
ftp-eval judges            # which judges can run here, and which cost money

# Try the whole pipeline with no prover and no API key.
ftp-eval verify -b mock --tasks examples/tasks.jsonl \
  --attempts examples/attempts.jsonl --out results.jsonl --k 1,2 --tactics

# Both links, from the three-file layout.
ftp-eval eval-all -b lean4 --judge claude --yes \
  --informal problems.jsonl --formal statements.jsonl --proofs proofs.jsonl
```

| command | what it does |
|---|---|
`backends` / `judges` | what can run here; `judges` flags which ones bill you |
`doctor -b NAME` | diagnose one backend; non-zero exit if unusable |
`verify` | link ②: proofs against statements, streamed to JSONL |
`check-statement` | link ①: formalizations against their prose |
`eval-all` | both, with a combined verdict |
`score results.jsonl --tactics` | pass@k, reward-hacking breakdown, tactic and structure metrics |
`compare a=x.jsonl b=y.jsonl` | rank several runs |
`preview` | print the exact source a backend would receive |

Run `preview` once per new dataset. A mis-set `assembly` makes every
attempt a syntax error and looks exactly like a terrible model.

### Long runs

```bash
ftp-eval verify -b lean4 -o project_dir=~/mathlib-project \
  --tasks minif2f.jsonl --attempts samples.jsonl \
  --out results.jsonl --timeout 300 -j 8 \
  --cache-dir .cache --resume --k 1,5,10 --strict
```

Results flush to `--out` as each lands, so you watch progress and keep
partial work if the run dies. `--resume` skips what is already there.
`--cache-dir` keys verdicts on (backend, statement, proof), so adding one
model to a comparison only pays for the new work. The run aborts after 20
consecutive harness errors rather than spending an hour on a broken
toolchain. `--strict` exits 4 on harness errors and 5 on reward hacking.

## Data format

Three JSONL files, joined on `task_id` — **not** on line order, because
three files that drift out of alignment would silently pair every problem
with the wrong formalization.

```jsonc
// informal.jsonl
{"task_id": "nat_add_zero", "informal_statement": "Show that n + 0 = n for every natural n."}

// formal.jsonl
{"task_id": "nat_add_zero", "header": "import Mathlib",
 "formal_statement": "theorem nat_add_zero (n : Nat) : n + 0 = n := by"}

// proof.jsonl  -- k samples per task, numbered
{"task_id": "nat_add_zero", "sample_index": 0, "model": "my-model", "proof": " simp"}
```

Field aliases are accepted (`problem`/`statement`/`nl`, `formal`/`theorem`,
`completion`/`output`), and unrecognized columns are preserved in
`metadata` rather than dropped. `assembly` says what the model was asked
to emit, per task:

| value | meaning |
|---|---|
`continue_statement` | header + statement + attempt (miniF2F: the model continues `:= by`) |
`full_file` | the attempt is a complete source file |
`header_plus_proof` | header + attempt, which restates the theorem |

Under the latter two the model supplies the statement, so it is checked
against the required one. Under `continue_statement` the harness
concatenates it and tampering is impossible by construction.

## Metrics

Recorded for **every** attempt, including failures and timeouts — how a
model fails is as informative as how it succeeds, and most of these are
only interesting as the comparison. Full reference in
[docs/metrics.md](docs/metrics.md).

| category | examples |
|---|---|
**Correctness** | unbiased pass@k, solve rate, per-split, per-model, pass rate by sample position, samples-to-first-success |
**Soundness** | reward hacking by class and by specific trick, kernel axiom audit, `integrity_ok` |
**Statement quality** | elaborates, non-trivial, non-vacuous, gold-equivalent, judge faithfulness |
**Failure modes** | ~35 specific Lean modes, plus **attribution**: model vs budget vs harness vs soundness |
**Success modes** | one-line automation / `decide` / term mode / short & long chains / structured steps / `calc` / cases / induction / helper lemmas |
**Proof structure** | declarations by kind, auxiliary lemmas, named steps, local dependency depth, cited lemmas, nesting, branch points, term vs tactic mode |
**Tactic usage** | frequency, opening & closing tactics, transition pairs, mean position, per-tactic success rate |
**Statement complexity** | binders by kind, hypotheses, conclusion size, quantifiers, connectives, distinct types |
**Repetition** | line / tactic / trigram repetition, consecutive duplicates, and duplication *across* the k samples |
**Comments** | segments by kind, doc comments, chars, ratio |
**Cost** | Lean compile time (separate from wall time), harness overhead, seconds per solved task, judge tokens and estimated cost |
**Signal search** | correlation of every numeric metric with pass/fail, ranked |

Everything is aggregated as distributions (mean / median / p25 / p75 / p90
/ max / stdev) and split verified vs failed. Percentiles sit next to the
mean because these are skewed: a few enormous proofs drag a mean away from
what a typical proof looks like.

Three of these earn their place by catching things a pass rate cannot:

* **Attribution.** "40% of failures were truncated output" is an
  instruction to raise `max_tokens`, not a capability result. Truncation
  is detected before the syntax patterns, because a cut-off completion
  *produces* a syntax error and reading that as "writes bad Lean" turns a
  budget setting into a claim about the model.
* **Success modes.** Two models at the same pass rate, one closing 90% of
  goals with a single `omega` and the other writing structured multi-step
  arguments, are not equally good — and the pass rate cannot say so.
* **Cross-sample duplication.** pass@k assumes k independent draws. If a
  model returns the same text five times the real sample size is one, and
  the report says so rather than quoting a precision the data lacks.

And two deliberate refusals: **global dependency depth** through the
library is not computed (it needs the prover's environment; only the local
subset is honest), and the correlation table carries **no p-values** — with
~25 metrics scanned at once some will correlate by chance, so it is
labelled a pointer, not a finding.

### Speed

The analysis layer runs on every attempt of every run, so its cost is
measured, budgeted and enforced rather than assumed.

| | |
|---|---|
whole layer, per attempt | **0.18 ms** |
1000-attempt run, all metrics | 0.18 s |
one `lake env lean` compile, for scale | 1–60 s |

```bash
python benchmarks/bench_metrics.py           # the numbers, reproducible
python benchmarks/bench_metrics.py --check   # enforce budgets (CI runs this)
```

Absolute budgets catch gross slowdowns, but they mean different things on
different machines. The actual guarantee is **complexity**: scaling tests
in `tests/test_performance.py` measure each hot path at size *n* and *4n*
and fail if the ratio approaches quadratic, which is machine-independent.
The guards are verified to fire — restoring the original quadratic
dependency walk makes one report 58.6× growth for a 4× input against a 9×
ceiling.

Two optimizations paid for themselves: the dependency walk went from
quadratic to linear (**140× faster** at 400 steps), and the ~40-pattern
reward-hacking screen gained a literal prefilter (**45.9× faster** on the
pattern loop). The second is only safe if a too-narrow trigger can never
silently disable a check, so every pattern carries an example it must
catch and a differential test proves the prefilter changes no verdict.
Details in [docs/metrics.md](docs/metrics.md#cost-of-measuring).

## Judges

Faithfulness is semantic, so it needs a model. That makes the judge the
weakest link, and the design is about not trusting it more than it
deserves:

* **Abstention is a real answer.** `JudgeLabel.UNSURE` exists; an
  abstention routes to a human instead of being averaged into a score.
* **Rejections get re-checked.** `--judge-samples 3` votes across samples,
  and a rejection draws extra samples before it stands. A single
  low-effort pass that says "unfaithful" and is never revisited quietly
  deletes correct work from a benchmark.
* **Disagreement is recorded, not smoothed.** Every sample's label is kept.
* **An outage is never a rejection.** An API failure raises `JudgeError`
  and is reported as unjudged.
* **Tokens are measured; dollars are estimated.** Usage comes from the
  provider; the dollar figure comes from a local price table, is labelled
  an estimate everywhere, and should be checked against billing.

```bash
ftp-eval check-statement --informal problems.jsonl --formal statements.jsonl \
  -b lean4 --judge claude --judge-samples 3 --yes
```

A paid judge refuses to run without `--yes`.

## Backends

| name | status | needs |
|---|---|---|
`mock` | complete | nothing; deterministic fake prover for CI and demos |
`lean4` | complete | a built Lean 4 project (`lake`, `.lake/`, Mathlib) |
`axle` | plumbing only | endpoint URL + credential, **and a response mapping you supply** |

### lean4

Batch compilation: one `lake env lean` per attempt, full elaboration, no
persistent session. Slower than driving a REPL, but its verdict is the
easy one to trust — the file either compiles against the Mathlib you
pinned or it does not. Lean messages are parsed into positions and
classified, the goal state stays attached to its error, and accepted
proofs get the `#print axioms` audit (a second compile; `-o
audit_axioms=false` to skip).

It also builds the statement probes: elaboration, triviality, vacuity and
gold-equivalence, each constructed from the statement's own binders so it
asks about the proposition the dataset actually contains.

```bash
export FTP_EVAL_LEAN_PROJECT=~/mathlib-project   # lake exe cache get && lake build
ftp-eval doctor -b lean4
```

### axle (and any other HTTP verification service)

Ships as **plumbing, not an integration**. Auth, retries, timeouts, error
classification and response mapping are here; the field names are not,
because these APIs are not stable public standards and guessing them is
how a harness reports `failed` for proofs that were actually accepted. An
unrecognized response shape raises rather than defaulting.

```python
create("axle", url="https://api.example.com/v1/verify",
       request_map={"source": "code"}, response_map={"ok": "proved"})
```

### Adding your own

One method. See [docs/adding-a-backend.md](docs/adding-a-backend.md):

```python
class MyProver(Verifier):
    name, language = "myprover", "mylang"

    def _verify(self, task, attempt, source, timeout_s):
        ok, log = my_prover.check(source, timeout=timeout_s)
        return RawVerdict.verified() if ok else RawVerdict.failed()
```

Assembly, soundness screening, timing, timeouts and error wrapping come
from the base class. Soundness screening is **not** opt-out-able.

## Interpreting results

| proof status | meaning |
|---|---|
`verified` | accepted, and no reward hacking found |
`rejected` | the prover accepted it, but it does not count — see `soundness.violations` |
`failed` | the prover ran and did not accept it |
`timeout` | still working when the budget ran out |
`error` | prover or harness broke; **excluded from the denominator** |
`skipped` | never attempted |

| combined status | meaning |
|---|---|
`solved` | faithful statement **and** valid proof. The only success |
`proved_wrong_statement` | valid proof of an unfaithful statement. Scores zero; this is what a proof-only harness counts as a win |
`unproved` | faithful statement, no valid proof. An honest miss |
`bad_statement` | formalization malformed or degenerate |
`hacked_proof` | proof was reward hacking |
`unverifiable` | proof valid but faithfulness never assessed, so no solve is claimed |

## Limitations

Stated plainly, since the point is trustworthy numbers.

* **The syntactic screen is not complete.** It catches the known tricks. A
  novel one will get past it — which is why the Lean axiom audit exists,
  and why `hack_patterns` names the specific trick so the list can grow.
* **Statement matching is textual.** A semantically equivalent
  reformulation gets flagged. That is the better failure direction: a
  `rejected` row is reviewable, a false `verified` is not.
* **The judge can be wrong in both directions.** Consensus and rechecking
  reduce it; they do not remove it. Audit the `UNSURE` and `UNFAITHFUL`
  items before trusting a headline.
* **Structural metrics are lexical.** Declaration and comment counts are
  exact; nesting and branch counts assume conventional formatting.
* **The `axle` backend is unverified against a live service.**
* **No sandbox.** A backend runs a prover on model-generated input, and
  Lean can read files and shell out. Run untrusted attempts in a container.
* **pass@k assumes i.i.d. draws** at one temperature. Mixed settings or
  deduplicated samples break the estimator's assumption.

## License

MIT
