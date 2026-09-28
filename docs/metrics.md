# Metrics reference

Everything the harness records, by category. Recorded for **every**
attempt, not only successes: how a model fails is as informative as how it
succeeds, and most of the categories below are only interesting as a
comparison between the two.

Each `results.jsonl` row carries the per-attempt fields; `ftp-eval score
results.jsonl --tactics` prints the aggregates, and `--json` emits all of
them.

---

## 1. Correctness

The headline numbers.

| metric | where | meaning |
|---|---|---|
`status` | per attempt | `verified` / `rejected` / `failed` / `timeout` / `error` / `skipped` |
`pass@k` | `Summary.pass_at` | Unbiased estimator (Chen et al. 2021). Tasks with fewer than k scoreable samples are dropped, not padded |
`solve_rate` | `Summary` | Fraction of *tasks* solved by ≥1 sample |
`attempt_pass_rate` | `Summary` | Fraction of scoreable *attempts* that verified |
`by_split` | `Summary` | All of the above, per split |
`by_sample_index` | `Summary` | Pass rate at each sample position. A steep decline means later samples are much worse — worth knowing before paying for k=10 |

`error` and `skipped` are excluded from the pass-rate denominator: an
attempt that never got a verdict is not evidence about the model.

## 2. Soundness / reward hacking

The category that decides whether category 1 means anything.

| metric | meaning |
|---|---|
`soundness.violations` | Per attempt, each tagged `[class:trick] message` |
`hack_classes` | Rolled up by class: `placeholder`, `new_axiom`, `kernel_bypass`, `resource_uncap`, `definition_shadowing`, `statement_tampering`, `elaboration_trick`, `homoglyph` |
`hack_patterns` | Rolled up by the **specific trick**: `lean.sorry`, `lean.axiom`, `kernel.sorry_ax`, `lean.hash_exit`, … This is the actionable cut — it names what to forbid next |
`integrity_ok` | False if anything errored or was rejected. If false, the headline number needs a caveat |

See [soundness.md](soundness.md) for the full check list. The short
version: **passing the kernel is not the bar.** `sorry` compiles with a
warning; `axiom cheat : <goal>` compiles with no complaint at all.

## 3. Statement quality

Only reachable if you supply the natural-language problem. These answer
"is this the right theorem?", which no amount of proof checking can.

| check | decided by |
|---|---|
`no_placeholder` | Statement itself contains no `sorry`/axiom |
`elaborates` | Prover: statement + placeholder body typechecks |
`non_trivial` | Prover: a cheap tactic does *not* close it |
`non_vacuous` | Prover: `False` is *not* derivable from the hypotheses |
`gold_equivalent` | Prover: `candidate ↔ reference` |
`judge_faithful` | LLM judge: does it mean what the prose says |

`checked_faithfulness` records whether meaning was actually assessed.
Passing only the structural checks is not evidence of faithfulness, and
the report says so rather than implying otherwise.

## 4. Proof structure

| metric | meaning |
|---|---|
`lines`, `code_lines`, `chars`, `tokens`, `max_line_chars` | Size |
`declarations` | Counts by kind: `theorem`, `lemma`, `def`, `instance`, … |
`auxiliary_declarations` | Helper lemmas beyond the theorem under test |
`named_steps` | `have` / `let` / `set` / `obtain` / `suffices` steps |
`local_dependency_depth` | Longest chain through the proof's **own** steps. Global depth through Mathlib needs the prover's environment and is deliberately not claimed |
`cited_lemmas` | Distinct qualified names cited — library reliance |
`calc_blocks` | Number of `calc` chains |
`max_nesting_depth` | Max of bracket depth and indentation depth |
`branch_points` | Goal-splitting tactics and combinators; the rough analogue of cyclomatic complexity |
`term_mode` | True when the proof has no `by` block at all |

Aggregated as distributions (mean / median / p25 / p75 / p90 / min / max /
stdev), **split by verified vs failed**. Percentiles are reported next to
the mean because these are skewed: a few enormous proofs drag a mean far
from what a typical proof looks like.

## 5. Tactic usage

| metric | meaning |
|---|---|
`tactics` | Per attempt: tactic names in order, repeats kept |
`total` | Times each tactic appears anywhere |
`proofs_using` | Number of proofs containing it at least once |
`in_verified` / `in_failed` | The same split by outcome |
`success_rate` | Fraction of proofs using it that verified. An **association**, not a causal claim — `omega` looking good may only mean it gets pointed at easy arithmetic |
`first_tactics` | What proofs **open** with |
`closing_tactics` | What proofs **close** with — which tactic actually discharges goals |
`transitions` | Ordered pairs (`simp->omega`), with a verified-only variant. The strategy *shape* rather than the ingredient list: two models with identical frequency tables can have completely different transitions |
`mean_position` | Per tactic, mean normalized position in the proof (0 = start, 1 = end) |
`mean_distinct_per_proof`, `mean_invocations_per_proof` | Strategy variety and verbosity |
`suspect_usage` | Counts for `sorry` / `admit` / `native_decide` |

A model that solves 40% with `nlinarith` alone is a different result from
one that solves 40% with twenty tactics.

## 5b. What carries signal

The reason for collecting this many metrics is to find which ones matter.
`Summary.correlations` does that search once per run: point-biserial
correlation between every numeric metric and pass/fail, ranked by
magnitude, with metrics below |r| = 0.1 dropped so the table stays
readable. Each row reports `r`, `n`, and the metric's mean among passes
vs failures.

Also aggregated for slicing: `by_model` (per-model rollup, so one results
file can hold a comparison), `by_split`, `by_sample_index`, and
`samples_to_first_success` (a low median means extra samples are mostly
wasted).

**These are associations.** A metric can track success because it tracks
task difficulty — `named_steps` correlating with failure may only mean
hard problems get long attempts. Treat a row as a pointer at something to
look at, not a finding. The report says so too.

## 5c. Statement complexity

Recorded on every result as `statement_*`, so solve rate can be correlated
against it — the closest thing to a difficulty axis available without
human labels.

| metric | meaning |
|---|---|
`statement_binders` | Total binders, plus `implicit_binders` and `instance_binders` |
`statement_hypotheses` | Binders whose type looks like a proposition |
`statement_conclusion_tokens` | Size of the goal itself, separate from its context |
`statement_quantifiers`, `statement_connectives` | `∀ ∃` and `∧ ∨ → ↔ ¬` counts |
`statement_cited_definitions` | Distinct qualified names the statement mentions |
`statement_distinct_types` | How many domains are in play at once |
`statement_total_tokens`, `statement_max_nesting_depth` | Overall size and depth |

Surface complexity, not mathematical depth — a short statement can be an
open problem. A proxy, not a difficulty score.

## 6. Repetition

Degenerate generation shows up here first — a looping model emits a long,
busy-looking proof that is the same line thirty times.

| metric | meaning |
|---|---|
`line_repetition_rate` | 1 − distinct/total over non-empty lines |
`tactic_repetition_rate` | 1 − distinct/total over tactic invocations |
`max_consecutive_duplicate_lines` | >2 is usually a loop |
`repeated_trigram_rate` | Standard rep-3 measure over tokens |
`duplication.mean_duplicate_fraction` | **Across** the k samples of a task |
`duplication.tasks_all_identical` | Tasks where every sample was byte-identical |

Cross-sample duplication bears directly on whether pass@k means anything:
the estimator assumes k independent draws, so if a model returns the same
text five times the real sample size is one. The report emits an explicit
warning when this crosses 25%.

## 7. Comments and documentation

| metric | meaning |
|---|---|
`comment_segments` | Total comment blocks + line comments |
`block_comments`, `doc_comments`, `line_comments` | By kind; `/-- -/` counted separately |
`comment_chars`, `comment_ratio` | Volume and share of the text |
`documented_proofs` | Proofs carrying at least one doc comment |

Comments are counted on the raw text and everything else on the stripped
text, so a heavily commented proof does not read as a structurally
complex one.

## 8. Cost and efficiency

| metric | meaning |
|---|---|
`wall_time_s` | Per attempt: total time inside `verify`, including screening and the axiom audit's second compile |
`compile_time_s` | Per attempt: **the prover's own** subprocess time. `None` if the backend does not measure it |
`compile_time` | Distribution over all attempts |
`compile_time_verified` | Restricted to accepted proofs. A timeout burns the full budget, so pooling the two hides how long a *successful* check takes |
`harness_overhead_s` | `total_wall_time − Σ compile_time` |
`seconds_per_solved_task` | The number to compare configurations with: +5% solve rate at 4× cost is a different trade from +5% for free |
`judge_usage` | Judge tokens in/out, cache reads, call count |
`estimated_cost_usd` | **An estimate** from a local price table, labelled as such everywhere. Tokens are measured; dollars are not. Verify against billing before quoting |

## 9. Failure modes

`error_kind` is coarse on purpose, so it means the same thing across
provers. `failure_mode` is the Lean-specific detail, because the coarse
version hides the distinctions that decide what to fix.

| metric | meaning |
|---|---|
`error_kind` | `syntax`, `type`, `unknown_identifier`, `unsolved_goals`, `tactic_failed`, `incomplete`, `timeout`, `resource_limit`, `soundness`, `toolchain`, `harness`, `unknown` |
`failure_mode` | One of ~35 specific modes, grouped below |
`attributions` | `model` / `budget` / `harness` / `soundness` — **whose problem it is** |
`budget_fraction` | Share of failures that are budget, not capability. Above ~10% the headline is measuring your harness |
`invented_names` | The lemma names the model made up, ranked. Directly useful for prompt work, retrieval, or spotting a Mathlib version mismatch |
`mean_first_error_position` | Where in the proof the first error lands, normalized. Near 0 = breaks immediately; near 1 = gets most of the way and fails at the last step |
`diagnostics` | Severity, message, line, column. The goal state under an "unsolved goals" error stays attached to it |

Mode groups: **syntax/truncation** (`truncated_output`, `unexpected_token`,
`incomplete_syntax`, `bad_indentation`, `empty_proof`) · **name
resolution** (`unknown_lemma`, `unknown_tactic`, `unknown_namespace`,
`ambiguous_name`) · **elaboration** (`type_mismatch`,
`application_mismatch`, `missing_instance`, `function_expected`,
`unresolved_metavariable`, `motive_not_type_correct`, `universe_issue`,
`numeral_type`) · **automation gave up** (`linarith_failed`,
`nlinarith_failed`, `omega_failed`, `simp_no_progress`,
`rw_pattern_not_found`, `decide_failed`, `positivity_failed`,
`ring_failed`, `aesop_failed`, `norm_num_failed`, `apply_failed`,
`exact_type_mismatch`, `other_tactic_failed`) · **incomplete**
(`unsolved_goals`, `placeholder_left`) · **resource**
(`heartbeat_exceeded`, `recursion_depth`, `wall_clock_timeout`,
`out_of_memory`) · **not the model** (`reward_hacking`,
`toolchain_error`, `harness_error`, `language_mismatch`).

Three distinctions worth the separate modes:

* **`truncated_output` vs `unexpected_token`.** A cut-off completion
  produces a syntax error. Reading that as "the model writes bad Lean"
  turns a `max_tokens` setting into a capability claim, so truncation is
  detected first — from `unexpected end of input`, unbalanced brackets, or
  an ending on a connector — and attributed to `budget`.
* **`unknown_lemma` vs `type_mismatch`.** One means the model invented
  `Nat.add_le_of_lt_succ`; the other means it misread the goal.
* **Which automation ran out of road.** `linarith_failed` and
  `simp_no_progress` are the same `tactic_failed` to `ErrorKind`, and
  which one it is is the most actionable fact about a failing benchmark.

## 10. Success modes

A verified proof still has a shape, and the shape is most of what you
learn from it. "Solved 40%" means something different when 90% of solves
are one `omega` call.

| metric | meaning |
|---|---|
`success_mode` | `one_liner_automation`, `brute_force_decide`, `term_mode`, `short_tactic_chain`, `long_tactic_chain`, `structured_with_steps`, `calc_chain`, `case_analysis`, `induction`, `auxiliary_lemmas` |
`automation_fraction` | Share of passes closed by automation alone |
`substantive_fraction` | Share that did structural work — cases, induction, named steps, helper lemmas |
`by_model` | The same distribution per model |

Assigned by the most structurally demanding feature present, so a proof
with both `induction` and a `calc` chain reports the induction. Not a
quality ranking: a one-line `omega` is the *right* proof for an arithmetic
goal. It is a description, so two models with the same pass rate can be
told apart — and so you notice a benchmark drifting into a computation
exercise as `brute_force_decide` climbs.

---

## Cost of measuring

Measured, not estimated, and enforced. Reproduce with
`python benchmarks/bench_metrics.py`; CI runs it with `--check`.

Windows AMD64, CPython 3.12, best-of-N:

| case | time | budget | of budget |
|---|---|---|---|
`verify` on a typical proof, **including every metric on this page** | **0.18 ms** | 10 ms | 1.8% |
`analyze_proof` on a 13 KB proof | 3.7 ms | 100 ms | 3.7% |
`analyze_proof` on a 400-step `have` chain | 5.5 ms | 200 ms | 2.8% |
`analyze_proof` on a 2000-step chain | 28.8 ms | 1000 ms | 2.9% |
`screen_source`, honest 13 KB proof | 1.1 ms | 100 ms | 1.1% |
`screen_source`, 13 KB containing a `sorry` | 1.3 ms | 100 ms | 1.3% |
`extract_tactics` on 13 KB | 0.8 ms | 50 ms | 1.6% |
`analyze_statement` | 0.04 ms | 5 ms | 0.7% |
`summarize` over 1000 results | 58 ms | 1000 ms | 5.8% |
`sample_duplication` over 1000 samples | 0.07 ms | 50 ms | 0.1% |
end to end: 1000 attempts + summarize | 138 ms | 10 s | 1.4% |

A 1000-attempt run spends about **0.18 s** on metrics. One `lake env lean`
compile is typically 1–60 s, so the prover dominates by three to five
orders of magnitude and the analysis layer is a rounding error.

### How the speed is guaranteed

Absolute budgets catch gross slowdowns but mean different things on
different machines. The real guarantee is complexity, checked by
**machine-independent scaling tests** in `tests/test_performance.py`: each
measures cost at size *n* and *4n* and fails if the ratio approaches
quadratic. Covered: `analyze_proof` (chained, wide and repetitive inputs),
`extract_tactics`, `screen_source` with and without statement checks,
`summarize`, and `sample_duplication`.

These guards are verified to actually fire. Re-introducing the original
quadratic dependency walk makes the chained test report **58.6× growth for
a 4× size increase** against a 9× ceiling.

### What was optimized, and why it is safe

* **Dependency depth was quadratic.** Regex-searching every earlier step
  name in every step's justification measured 177 ms at 400 steps.
  Tokenizing each justification once and intersecting against the names
  seen so far is linear — 1.2 ms at 400 steps, **140× faster**.
* **The reward-hacking screen ran ~40 regexes over the whole body.** Each
  pattern now declares `requires`, a set of lowercase literal triggers
  checked with a substring test before the regex runs. An honest proof
  contains none of them, so almost every pattern is ruled out by a C-level
  scan: **45.9× faster** on the pattern loop, 3.2 ms → 1.1 ms overall.

  This optimization is only safe if a too-narrow trigger set can never
  silently switch a check off — and that failure would be invisible, since
  every positive-detection test supplies the trigger itself. So each
  pattern also carries an `example` it must catch, and two tests enforce
  the implication: `test_prefilter_admits_every_pattern_example` and
  `test_prefilter_gives_the_same_verdicts_as_no_prefilter`, a differential
  test over every example plus honest proofs. A wrong trigger fails the
  suite rather than quietly disabling reward-hacking detection.
* **Tokenization and tactic extraction ran twice per attempt** — once for
  the size metrics, once for repetition. Computed once and passed through.
* **Trigram counting** no longer materializes a list of *n*−2 tuples.

## Not computed, and why

Stated so that nothing here looks more capable than it is.

* **Global dependency depth** through the library. Needs the prover's
  environment, not the proof text. `local_dependency_depth` is the honest
  subset.
* **Goal-state metrics** (goals opened/closed, hypothesis counts during
  the proof). Needs prover introspection — a REPL backend could add it.
* **Semantic proof similarity.** Repetition here is lexical. Two proofs
  that are the same idea in different syntax read as distinct.
* **Compression vs a reference proof.** Would need gold proofs, which the
  dataset format does not require.
* **Statistical significance** on the correlations. No p-values, no
  multiple-comparison correction, and with ~25 metrics scanned at once
  some will correlate by chance. That is why the threshold is a magnitude
  and the output is labelled a pointer rather than a result.
