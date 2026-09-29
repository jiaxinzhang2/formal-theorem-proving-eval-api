# Examples

Every one runs as-is on the `mock` backend — none needs Lean installed.

## The format

**Lean files**, and nothing else. A problem is one `.lean` file with one
theorem; an answer is another `.lean` file; a benchmark is a folder of
problems. That is the whole input contract.

| | |
|---|---|
grade one answer | `ftp-eval match theorem.lean answer.lean` |
grade a benchmark | `ftp-eval grade --problems … --submissions …` |
check the problems themselves | `ftp-eval audit --problems …` |
the format spec | [`../benchmarks/README.md`](../benchmarks/README.md) |
a full benchmark | [`../benchmarks/demo-2026/`](../benchmarks/demo-2026/) |

## [`answer-verdicts/`](answer-verdicts/) — one problem, six answers

```bash
ftp-eval match examples/answer-verdicts/theorem.lean \
                examples/answer-verdicts/answer-*.lean
```

Each answer demonstrates a different verdict:

| file | verdict | why |
|---|---|---|
`answer-good.lean` | matched | hole filled, vocabulary kept, proved |
`answer-with-helpers.lean` | matched | brings its own definitions and lemmas — legitimate, not penalised |
`answer-unproved.lean` | matched_but_unproved | right statement, no proof yet. An honest miss, not cheating |
`answer-weakened.lean` | mismatched | `IsLeast` became plain membership |
`answer-gutted.lean` | mismatched | theorem text byte-identical; the definition under it was replaced by `True` |
`answer-smuggled.lean` | mismatched | a helper the proof cites is left `sorry`-backed |

The bottom three are the point: **each one compiles on its own merits**, and
none of them proves the problem. That is why grading cannot be "does it
compile".

To see what the prover would actually be handed for any of them:

```bash
ftp-eval match --show-probe examples/answer-verdicts/theorem.lean \
                            examples/answer-verdicts/answer-gutted.lean
```

The probe states the **theorem file's** proposition over the **theorem
file's** definitions, closed with the answer's proof term — which is why
the gutted `abbrev` in the answer cannot help it.

## Grading a whole benchmark

The demo benchmark in [`../benchmarks/demo-2026/`](../benchmarks/demo-2026/)
is three problems and three participants, including a misfiled submission
and an answer that leaves the problem's `sorry` in place:

```bash
ftp-eval grade -b mock \
  --problems ../benchmarks/demo-2026/problems \
  --submissions ../benchmarks/demo-2026/submissions \
  --out /tmp/results
```

Then look at `/tmp/results/<run-id>/`: `1-match/refused.jsonl` names the
trick behind each refusal, `2-compile/probes/` holds the exact source each
prover call received, and `answers/<who>/<problem>.json` carries everything
known about one answer, metrics included.

## Auditing the problems before publishing them

```bash
ftp-eval audit --problems ../benchmarks/demo-2026/problems
```

The demo problems are well formed, so nothing comes back malformed — note
in particular that their `sorry` bodies are *not* flagged, because a
problem file's `sorry` is the hole a participant fills, and their
`variable` bindings are ordinary Lean. Screening a statement with the
proof-side rules would report all three as broken.

Without a prover and a judge the verdicts are `inconclusive`, which is the
honest answer rather than a pass. Add both to get a real one:

```bash
ftp-eval audit --problems ../benchmarks/demo-2026/problems \
  -b lean4 --judge claude --yes
```

The audit also reports the setter's own gaps separately from the verdicts —
problems with no `prose:` (so faithfulness could not be judged) and
problems with no MathDB id or source (so a published result could not be
traced back). The demo problems record all of it; delete a `- prose:` line
from one and re-run to see the report.
