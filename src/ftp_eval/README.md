# `ftp_eval`

Read [`spec/`](spec/) first. It is pure interface — abstract base classes
and the shapes they exchange, no logic — so it is the short version of the
whole design.

```
spec/           THE CONTRACTS
formalizing/    ① is the formalization faithful to the problem?
proving/        ② does the proof close the goal?
analysis/       ③ what happened, passes and failures alike
grading/        the contest pipeline: runs ①→②→③
end_to_end.py   the dataset pipeline (JSONL, many samples, pass@k)
cli.py          ftp-eval
```

Shared by every layer, which is why they sit here rather than inside one:

```
types.py        the vocabulary every layer speaks
soundness.py    reward-hacking detection
comments.py     what counts as a comment, per language
dataset.py      JSONL I/O and the three-file layout
registry.py     backend and judge lookup by name
```

The whole public API is importable straight from `ftp_eval`; these paths
matter only when extending the package. Full map in
[../../ARCHITECTURE.md](../../ARCHITECTURE.md).
