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
  run.json                       schema v3, lifecycle, policy, toolchain, labels
  inputs/problems/<id>.lean       exact benchmark inputs before grading
  inputs/submissions/<who>/<id>.lean   exact supplied answers
  problems.json / problems.tsv    provenance and content hashes
  events.jsonl                   chronological stage history
  0-environment/
    stage.json                   backend capability and exact environment preflight
  1-interface/
    all.jsonl / refused.jsonl
    by-answer/<who>/<id>.json     latest stage checkpoint
  2-kernel/
    all.jsonl
    by-answer/<who>/<id>.json
    modules/<who>/<id>/...        problem, optional trusted Goal, answer, Check sources
    logs/<who>/<id>.log
  3-replay/
    all.jsonl / by-answer/<who>/<id>.json  fresh leanchecker kernel replay
  4-dependencies/
    all.jsonl / by-answer/<who>/<id>.json  transitive axiom extraction
  5-axioms/
    all.jsonl
    by-answer/<who>/<id>.json
  answers/<who>/<id>.json         final verdict and metrics, saved per answer
  6-report/
    stage.json                   report running/passed checkpoint
    by-problem.tsv
    by-participant.tsv
    reasons.tsv
  leaderboard.tsv
  summary.json / summary.txt
```

The manifest is written before processing answers. Interface, kernel, replay, dependency and axiom
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
a failed module capability preflight aborts the run before grading answers.
Strict evaluation also requires exact Lean, dependency manifest and worker image pins. A hard process kill may leave `running`.

These checkpoints support inspection and recovery of completed work. Automatic
resume and model API retry/generation are not implemented. A new invocation
starts a new run.

Stage directory names are defined by `StageId.directory`; tests derive paths from
that enum. Schema v3 intentionally changes the previous phase numbering. Replay
checks compiled declarations with Lean’s built-in kernel; it is not an independently
implemented proof checker. Cloud workers can implement `ContainerExecutor` without
changing benchmark grading.
