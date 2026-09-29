# Run lifecycle and checkpoints

One API/model answer group is evaluated as one run. Its answers are supplied as
submission files; this package does not generate them through a model API.
Use `run_metadata` / `--run-metadata` to identify the experiment and sampling
configuration. Do not put credentials in experiment labels.

The CLI saves runs by default under `runs/`. Library callers choose `output_dir`;
omitting it returns in-memory results without persistence. Default run ids include
UTC microseconds and a random suffix. Existing ids are refused rather than reused.

```text
runs/<run-id>/
  run.json                       schema v2, lifecycle, policy, toolchain, labels
  inputs/problems/<id>.lean       exact benchmark inputs before grading
  inputs/submissions/<who>/<id>.lean   exact supplied answers
  problems.json / problems.tsv    provenance and content hashes
  events.jsonl                   chronological stage history
  1-interface/
    all.jsonl / refused.jsonl
    by-answer/<who>/<id>.json     latest stage checkpoint
  2-kernel/
    all.jsonl
    by-answer/<who>/<id>.json
    modules/<who>/<id>/...        problem, answer, Check sources
    logs/<who>/<id>.log
  3-axioms/
    all.jsonl
    by-answer/<who>/<id>.json
  answers/<who>/<id>.json         final verdict and metrics, saved per answer
  4-report/
    stage.json                   report running/passed checkpoint
    by-problem.tsv
    by-participant.tsv
    reasons.tsv
  leaderboard.tsv
  summary.json / summary.txt
```

The manifest is written before processing answers. Interface, kernel and axiom
stages emit `running` followed by `passed`, `failed` or `not_run`. Each event has
participant, problem id, UTC timestamp, detail and structured payload. Stages
skipped after a refusal receive `not_run` records too. The report stage emits its
own run-level checkpoints.

The history is append-only and flushed to disk; stage snapshots and manifests
are replaced atomically. A completed answer is saved before starting the next.
Kernel payloads include backend diagnostics and raw responses when provided.

Lifecycle values in `run.json` are `running`, `complete`, `failed`, `interrupted`.
`complete` means grading finished, not that all answers passed. Backend failures
are answer results and do not abort the group. A missing audit is a refusal;
an unsupported backend is `not_run`. A hard process kill may leave `running`.

These checkpoints support inspection and recovery of completed work. Automatic
resume and model API retry/generation are not implemented. A new invocation
starts a new run.
