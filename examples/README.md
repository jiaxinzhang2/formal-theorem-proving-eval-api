# Examples

Every one runs as-is on the `mock` backend — none needs Lean installed.

## The format this is for

**Lean files.** A problem is one `.lean` file with one theorem; an answer is
another `.lean` file. A benchmark is a folder of problems. That is the whole
input contract.

| | |
|---|---|
grade one answer | `ftp-eval match theorem.lean answer.lean` |
grade a benchmark | `ftp-eval grade --problems … --submissions …` |
the format spec | [`../benchmarks/README.md`](../benchmarks/README.md) |
a full benchmark | [`../benchmarks/demo-2026/`](../benchmarks/demo-2026/) |

### [`answer-verdicts/`](answer-verdicts/) — one problem, six answers

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

## The other input mode

### [`jsonl-mode/`](jsonl-mode/) — JSONL datasets, many samples, pass@k

A different shape for a different job: benchmarking a *model* over k sampled
attempts per task, rather than grading a participant's submitted file. Same
checks underneath, different unit of work.

```bash
# k samples per task -> pass@k
ftp-eval verify -b mock \
  --tasks examples/jsonl-mode/tasks.jsonl \
  --attempts examples/jsonl-mode/attempts.jsonl \
  --k 1,2 --tactics

# prose + formal statement + proof, as three joined files
ftp-eval eval-all -b mock --judge mock --yes \
  --informal examples/jsonl-mode/triplet/informal.jsonl \
  --formal   examples/jsonl-mode/triplet/formal.jsonl \
  --proofs   examples/jsonl-mode/triplet/proof.jsonl
```

`tasks`/`attempts`: four tasks, two samples each; one attempt is a `sorry`
dressed as a pass, so the run reports reward hacking and `--strict` exits
non-zero.

`triplet`: four tasks, of which two have **valid proofs of unfaithful
statements** — which a proof-only harness scores as successes, and which
this reports as `proved_wrong_statement`.
