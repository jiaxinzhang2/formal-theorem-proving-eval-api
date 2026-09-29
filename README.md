# formal-theorem-proving-eval-api

Grading formal theorem proving from Lean files, for people who have to
defend the numbers afterwards.

**New here?** Two minutes:

| you want to | go to |
|---|---|
understand the design | [ARCHITECTURE.md](ARCHITECTURE.md), then [`src/ftp_eval/spec/`](src/ftp_eval/spec/) — the contracts, as ABCs the real classes implement |
run a contest: N Lean problems, many participants | [`benchmarks/README.md`](benchmarks/README.md) |
grade one answer against one problem | `ftp-eval match theorem.lean answer.lean` |
see every verdict on a worked example | [`examples/README.md`](examples/README.md) |
know what counts as cheating, and what does not | [`docs/soundness.md`](docs/soundness.md) |
know what gets measured | [`docs/metrics.md`](docs/metrics.md) |
plug in a prover | [`docs/adding-a-backend.md`](docs/adding-a-backend.md) |

Nothing below needs Lean installed — every example runs on the `mock`
backend.

## One input format

Lean files. Nothing else:

| thing | is |
|---|---|
| a **problem** | one `.lean` file, one theorem |
| an **answer** | one `.lean` file |
| a **benchmark** | a folder of problems |
| a **submission** | a folder per participant |

```bash
ftp-eval match theorem.lean answer.lean            # one answer, one problem
ftp-eval grade --problems P/ --submissions S/      # a whole benchmark
ftp-eval audit  --problems P/                      # check the problems themselves
```

## Two questions, two APIs

```
natural language  ──①──▶  formal statement  ──②──▶  formal proof
                  faithful?                 proves it?
                  autoformalization/        proving/
```

They are separate, and they stay separate — neither package imports the
other, and a test enforces it. You run ① **before** publishing a problem
set and ② when answers come in. The reason to keep them apart is that
fusing them is how a suspicious statement with a valid proof gets counted
as solved.

```python
from ftp_eval import match_submission

report = match_submission(open("theorem.lean").read(), open("answer.lean").read())
print(report.verdict)   # e.g. "mismatched: dependency_changed"
```

Zero runtime dependencies — the core is standard library only. The Claude
judge needs `anthropic`; nothing else does.

## Passing the kernel is not the bar

The load-bearing idea. A prover's "yes" does not mean a proof exists:

| what the answer contains | what the kernel does |
|---|---|
`sorry` | **Compiles.** Lean reports a *warning*; exit code 0 |
`axiom cheat : <the goal>` | **Compiles with no complaint at all.** Asserting a proposition as an axiom is, to the kernel, perfectly well-formed |
`variable (h : False)` | **Compiles.** Lean silently adds it as a hypothesis, weakening the theorem to nothing |
`#exit` | **Compiles.** Lean stops reading the file; anything after is never checked |
contradictory hypotheses | **Compiles, and the proof is genuinely valid.** Anything follows from a contradiction, so the theorem says nothing |
`native_decide` | **Compiles.** Trusted from the compiler, not checked by the kernel |

So every accepted proof is screened above the backend layer, and a proof
that trips the screen comes back `REJECTED`, never `VERIFIED`. Backends
cannot opt out. For Lean there is a second, stronger layer: a `#print
axioms` audit, the only check that sees through indirection — a `sorry`
inside a helper lemma three files away still shows up in the axiom set, and
a declared axiom shows up nowhere else.

Recorded by class **and** by specific trick, so the list can grow:

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

**Not every class applies to every file.** A problem file's `sorry` is the
hole a participant fills, and its `variable (n : Nat)` is ordinary Lean —
so `ftp-eval audit` screens statements for a deliberately smaller set
(`STATEMENT_HACK_CLASSES`) than `ftp-eval grade` screens answers for.
Screening a statement with the proof-side rules is not a harmless
superset: it reports every well-formed problem as malformed.

Vacuous hypotheses are handled on the *statement* side
(`ProbeKind.VACUOUS`), because there the proof is genuinely valid and only
the statement is at fault.

## Does `answer.lean` prove `theorem.lean`?

```bash
ftp-eval match theorem.lean answer.lean
```

```
target:  demo_least_N_3
verdict: mismatched
text screen: mismatched
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

Four properties of the format make the naive answer wrong, and each is
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
`= answer(4)`, so the statement legitimately changes and verbatim
comparison would flag every solved problem. The holes are normalized away;
everything else — binders, implicitness, bounds, `=` vs `≤` — is compared
exactly.

Runnable fixtures in [examples/answer-verdicts/](examples/answer-verdicts/):
an honest answer, one with helper definitions, one that weakens the claim,
one that guts a definition, one that leaves a cited helper unproved, one
with no proof yet.

## Grading a benchmark

Three stages, and every stage's conclusion is recorded:

```bash
ftp-eval grade -b lean4 -o project_dir=~/mathlib-project \
  --problems benchmarks/my-2026/problems \
  --submissions benchmarks/my-2026/submissions \
  --out results --strict
```

| stage | what it decides | if it cannot run |
|---|---|---|
**1 match** | is this an answer to *this* problem? Text only, so it is cheap and runs first | — |
**2 compile** | does the prover accept the answer's proof *of the problem's proposition*? Authoritative | `NOT_RUN`, never folded into passed or failed |
**3 report** | statistics over correct **and** incorrect answers | — |

Stage 3 measures every answer, including the ones stage 1 refused — those
never reach a prover, so nothing else would record their shape, and they
carry most of the signal.

The results directory is laid out so a newcomer can find things and a
sceptic can re-run them:

```
results/<run-id>/
├── run.json                 versions, hashes, counts, warnings
├── problems.json/.tsv       per-problem metadata, including the MathDB id
├── leaderboard.tsv          who solved what
├── summary.txt/.json
├── 1-match/all.jsonl        every stage-1 verdict
├── 1-match/refused.jsonl    just the refusals, with the trick named
├── 2-compile/all.jsonl      every stage-2 verdict
├── 2-compile/probes/…       the exact source the prover was given
├── 2-compile/logs/…         the prover's own output
├── 3-report/*.tsv           by problem, by participant, by reason
└── answers/<who>/<prob>.json  everything known about one answer
```

Full layout and naming rules in [ARCHITECTURE.md](ARCHITECTURE.md).

## Auditing your own problem set

Run this before anyone answers. A vacuous or trivially-true statement is
the setter's bug, and it is better found once over the problem folder than
inferred later from a leaderboard on which everybody scored.

```bash
ftp-eval audit --problems benchmarks/my-2026/problems \
  -b lean4 --judge claude --judge-samples 3 --yes
```

**Only one of its checks can confirm a problem; the rest can only refuse
one.** Worth being blunt about, because it decides whether `audit` is
useful to you:

| check | who decides | can it say *yes*? |
|---|---|---|
assumes nothing it should prove | text only | no — refuse only |
elaborates | the prover | no — refuse only |
not closable by `trivial` | the prover | no — refuse only |
no contradictory hypotheses | the prover | no — refuse only |
**faithful to the prose** | **an LLM judge** | **yes** |
iff-equivalent to a reference | the prover | yes, if you wrote a reference |

A statement can pass all four screens and still formalize a problem nobody
asked about, so passing them yields `inconclusive`, never `ok`. This is the
real asymmetry with grading: there the kernel is ground truth and no model
is involved. Here **there is no ground truth** — "does this Lean say what
that English says" is not a question a prover can be asked — so the only
affirmative answer comes from a judge that can be wrong. Everything under
[Judges](#judges) exists because of that.

Problems with no `prose:` are reported as unjudged rather than passed, and
the judge is not called for them at all: judging against nothing spends
money to learn nothing.

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

Six commands.

| command | what it does |
|---|---|
`match theorem.lean answer.lean …` | does the answer prove the theorem? `--show-probe` prints the source a prover would get |
`grade --problems P/ --submissions S/` | the three-stage pipeline over a benchmark |
`audit --problems P/` | check the problem set itself |
`backends` / `judges` | what can run here; `judges` flags which ones bill you |
`doctor -b NAME` | diagnose one backend; non-zero exit if unusable |

Exit codes: `0` fine, `2` usage, `3` backend or judge unavailable, `5`
something was refused (`--strict`).

Two habits worth having. Run `ftp-eval audit` on a new problem set before
publishing it. Run `ftp-eval match --show-probe` on one answer before a
long grading run: it prints exactly what the prover will be handed, and a
misassembled probe looks identical to a participant who cannot prove
anything.

## Problem file format

One theorem per file, formal-conjectures style, with a metadata block:

```lean
import Mathlib

/-!
# Least N for a 3-element sum-distinct set

## Provenance
- mathdb_id: demo.001
- source: demo-2026
- source_locator: problems/P001.lean
- difficulty: unrated
- author: demo
- prose: Find the least N such that there exists a three-element subset A
  of {1, ..., N} all of whose subset sums are distinct.
-/

namespace Demo

/-- $A\subseteq\{1,\dots,N\}$ with all subset sums distinct. -/
abbrev IsSumDistinctSet (A : Finset ℕ) (N : ℕ) : Prop := …

@[category research open, AMS 5 11]
theorem demo_least_N_3 :
    IsLeast { N | ∃ A, IsSumDistinctSet A N ∧ A.card = 3 } answer(sorry) := by
  sorry

end Demo
```

Unrecognized metadata keys are kept, not dropped — a setter's own
bookkeeping field is not ours to discard. `prose:` is optional for grading
and required for `audit` to judge faithfulness. Benchmark-wide facts,
including the Lean toolchain and Mathlib revision, go in `benchmark.json`;
the grader reconciles what was declared against what it actually ran and
warns on a mismatch. Format spec in
[`benchmarks/README.md`](benchmarks/README.md).

## Metrics

Recorded for **every** answer, refused ones included — how an answer fails
is as informative as how it succeeds, and a metric that exists only on
successes cannot be compared against anything. Full reference in
[docs/metrics.md](docs/metrics.md).

| category | examples |
|---|---|
**Correctness** | solved, per problem, per participant, stage reached |
**Soundness** | reward hacking by class and by specific trick, kernel axiom audit |
**Statement quality** | elaborates, non-trivial, non-vacuous, gold-equivalent, judge faithfulness |
**Failure modes** | ~35 specific Lean modes, plus **attribution**: model vs budget vs harness vs soundness |
**Success modes** | one-line automation / `decide` / term mode / short & long chains / structured steps / `calc` / cases / induction / helper lemmas |
**Proof structure** | declarations by kind, auxiliary lemmas, named steps, local dependency depth, cited lemmas, nesting, branch points, term vs tactic mode |
**Tactic usage** | frequency, opening & closing tactics, transition pairs, mean position, per-tactic success rate |
**Statement complexity** | binders by kind, hypotheses, conclusion size, quantifiers, connectives, distinct types |
**Repetition** | line / tactic / trigram repetition, consecutive duplicates, duplication across answers |
**Comments** | segments by kind, doc comments, chars, ratio |
**Cost** | Lean compile time (separate from wall time), harness overhead, judge tokens and estimated cost |
**Signal search** | correlation of every numeric metric with solved/not, ranked |

Aggregated as distributions (mean / median / p25 / p75 / p90 / max /
stdev) and split solved vs not. Percentiles sit next to the mean because
these are skewed: a few enormous proofs drag a mean away from what a
typical proof looks like.

Two of these earn their place by catching what a solve rate cannot:

* **Attribution.** "40% of failures were truncated output" is an
  instruction to raise `max_tokens`, not a capability result. Truncation is
  detected before the syntax patterns, because a cut-off answer *produces*
  a syntax error and reading that as "writes bad Lean" turns a budget
  setting into a claim about the model.
* **Success modes.** Two participants at the same solve rate, one closing
  90% of goals with a single `omega` and the other writing structured
  multi-step arguments, are not equally good.

And two deliberate refusals: **global dependency depth** through the
library is not computed (it needs the prover's environment; only the local
subset is honest), and the correlation table carries **no p-values** — with
~35 metrics scanned at once some will correlate by chance, so it is
labelled a pointer, not a finding.

### Speed

The analysis layer runs on every answer of every run, so its cost is
measured, budgeted and enforced rather than assumed.

| | |
|---|---|
a backend verdict, no metrics | 0.10 ms |
measuring one answer, every metric | **0.24 ms** |
100 problems × 20 participants, all three stages | 0.94 s |
one `lake env lean` compile, for scale | 1–60 s |

The first two rows are separate because the code is: a backend reports what
the prover said, and `proving/analysis/measure.py` turns that into metrics.

```bash
python tests/measure_speed.py           # the numbers, reproducible
python tests/measure_speed.py --check   # enforce budgets (CI runs this)
```

Absolute budgets catch gross slowdowns but mean different things on
different machines. The actual guarantee is **complexity**: scaling tests
in `tests/test_performance.py` measure each hot path at *n* and *4n* and
fail if the ratio approaches quadratic, which is machine-independent. The
guards are verified to fire — restoring the original quadratic dependency
walk makes one report 58.6× growth for a 4× input against a 9× ceiling.

Two optimizations paid for themselves: the dependency walk went from
quadratic to linear (**140× faster** at 400 steps), and the reward-hacking
screen gained a literal prefilter (**45.9× faster** on the pattern loop).
The second is only safe if a too-narrow trigger can never silently disable
a check, so every pattern carries an example it must catch and a
differential test proves the prefilter changes no verdict.

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
* **It is not called when there is nothing to judge.** A problem with no
  `prose:` is reported as unjudged rather than sent to the API.
* **Tokens are measured; dollars are estimated.** Usage comes from the
  provider; the dollar figure comes from a local price table, is labelled
  an estimate everywhere, and should be checked against billing.

A paid judge refuses to run without `--yes`.

## Backends

| name | status | needs |
|---|---|---|
`mock` | complete | nothing; deterministic fake prover for CI and demos |
`lean4` | complete | a built Lean 4 project (`lake`, `.lake/`, Mathlib) |
`axle` | plumbing only | endpoint URL + credential, **and a response mapping you supply** |

### lean4

Batch compilation: one `lake env lean` per attempt, full elaboration, no
persistent session. Slower than driving a REPL, but its verdict is the easy
one to trust — the file either compiles against the Mathlib you pinned or
it does not. Lean messages are parsed into positions and classified, the
goal state stays attached to its error, and accepted proofs get the `#print
axioms` audit (a second compile; `-o audit_axioms=false` to skip).

It also builds the statement probes: elaboration, triviality, vacuity and
gold-equivalence, each constructed from the statement's own binders so it
asks about the proposition the problem actually contains.

```bash
export FTP_EVAL_LEAN_PROJECT=~/mathlib-project   # lake exe cache get && lake build
ftp-eval doctor -b lean4
```

### axle (and any other HTTP verification service)

Ships as **plumbing, not an integration**. Auth, retries, timeouts, error
classification and response mapping are here; the field names are not,
because these APIs are not stable public standards and guessing them is how
a harness reports `failed` for proofs that were actually accepted. An
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
from the base class, and soundness screening is not opt-out-able. What a
backend does **not** do is measure the proof — that belongs to
`proving/analysis/`, so your backend stays a thin adapter.

## Interpreting results

| answer status | meaning |
|---|---|
`verified` | accepted, and no reward hacking found |
`rejected` | the prover accepted it, but it does not count — see `soundness.violations` |
`failed` | the prover ran and did not accept it |
`timeout` | still working when the budget ran out |
`error` | prover or harness broke; **excluded from the denominator** |
`skipped` | never attempted |

| match verdict | meaning |
|---|---|
`matched` | states the theorem that was set, and supplies a proof |
`matched_but_unproved` | right statement, no proof. An honest miss |
`mismatched` | states something else — the mismatch names which property |
`missing` | the target theorem is not in the file |
`unparsed` | the file could not be read as Lean |

A `matched` at stage 1 is **not** a claim that the proof works. Only stage
2 can say that, and `kernel_checked` records whether it ran.

## Limitations

Stated plainly, since the point is trustworthy numbers.

* **Nothing here has run against a real Lean toolchain or real model
  output.** The reward-hacking patterns are implemented and tested against
  fixtures; they are not an empirical finding about what models do.
* **The syntactic screen is not complete.** It catches the known tricks. A
  novel one will get past it — which is why the Lean axiom audit exists,
  and why each detection names the specific trick so the list can grow.
* **Statement matching is textual.** A semantically equivalent
  reformulation gets flagged at stage 1. That is the better failure
  direction, and stage 2 settles it: a refusal is reviewable, a false
  `verified` is not.
* **The judge can be wrong in both directions.** Consensus and rechecking
  reduce it; they do not remove it. Audit the `UNSURE` and `UNFAITHFUL`
  items before trusting a headline.
* **Structural metrics are lexical.** Declaration and comment counts are
  exact; nesting and branch counts assume conventional formatting.
* **The `axle` backend is unverified against a live service.**
* **No sandbox.** A backend runs a prover on participant-supplied input,
  and Lean can read files and shell out. Grade untrusted submissions in a
  container.

## License

MIT
