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
`mean_distinct_per_proof` | Strategy variety |
`suspect_usage` | Counts for `sorry` / `admit` / `native_decide` |

A model that solves 40% with `nlinarith` alone is a different result from
one that solves 40% with twenty tactics.

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

## 9. Error analysis

| metric | meaning |
|---|---|
`error_kind` | `syntax`, `type`, `unknown_identifier`, `unsolved_goals`, `tactic_failed`, `incomplete`, `timeout`, `resource_limit`, `soundness`, `toolchain`, `harness`, `unknown` |
`error_kinds` | Histogram over the run |
`diagnostics` | Per attempt: severity, message, line, column. For Lean, the goal state under an "unsolved goals" error stays attached to it |
`per_task.first_error_kind` | What each task failed on first |

---

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
* **Tactic transition probabilities** (which tactic follows which). The
  per-attempt `tactics` list is ordered, so this is computable downstream;
  it is just not aggregated here.
