#!/usr/bin/env python3
"""Measure how fast the analysis layer runs, and enforce speed budgets.

Nothing to do with the *problem* metrics in ``docs/metrics.md`` being
measured -- this measures the cost of computing them. It lives in
``tests/`` beside ``test_performance.py``, which holds the complexity
guarantees these budgets complement.

Committed so the speed claims in the docs are checkable. Run it before and
after touching :mod:`ftp_eval.proving.analysis.structure`, :mod:`ftp_eval.backends.soundness`,
:mod:`ftp_eval.proving.analysis.tactics` or :mod:`ftp_eval.proving.analysis.modes`.

    python scripts/measure_speed.py            # human-readable table
    python scripts/measure_speed.py --json     # machine-readable
    python scripts/measure_speed.py --check    # enforce budgets, exit 1 on breach

``--check`` is what CI runs. Its budgets are deliberately loose -- roughly
20-50x over the measured figures -- because the job of the absolute bounds
is to catch a gross regression, while the complexity guarantees live in
``tests/test_performance.py`` as machine-independent scaling tests.

Timings are best-of-N: the minimum is the least noisy estimator of true
cost, since scheduler noise only ever adds time.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from typing import Callable

from ftp_eval import (
    ProofAttempt,
    ProofTask,
    analyze_proof,
    analyze_statement,
    create,
    extract_tactics,
    check_interface,
    read_interface_problem,
    screen_source,
)
from ftp_eval.proving.analysis.structure import sample_duplication
from ftp_eval.proving.analysis.measure import measure
from ftp_eval.proving.grading import ProblemSet, Submission, grade_contest

#: name -> seconds. A breach means something got much slower, not slightly.
BUDGETS: dict[str, float] = {
    "verify: one proof (no metrics)": 0.002,
    "measure: one proof (all metrics)": 0.010,
    "analyze_proof: 13KB proof": 0.100,
    "analyze_proof: 400-step chain": 0.200,
    "analyze_proof: 2000-step chain": 1.000,
    "screen_source: 13KB honest proof": 0.100,
    "screen_source: 13KB with a sorry": 0.100,
    "extract_tactics: 13KB proof": 0.050,
    "analyze_statement: typical statement": 0.005,
    "sample_duplication: 1000 samples": 0.050,
    "check_interface: one answer": 0.010,
    "grade: 100 problems x 1 participant": 2.000,
    "grade: 100 problems x 20 participants": 20.000,
}


TYPICAL_PROOF = (
    " have key : 0 < n := h\n"
    "  have step : n * 1 <= n * n := Nat.mul_le_mul_left n h\n"
    "  -- finish with arithmetic\n"
    "  nlinarith [key, step, sq_nonneg n]"
)
TYPICAL_STATEMENT = "theorem t {α : Type} [LinearOrder α] (n : Nat) (h : 0 < n) : n ^ 2 >= n := by"

#: A problem file and an answer to it, %s-templated on the problem id, in
#: the shape the grading pipeline reads: one theorem per file.
PROBLEM_TEMPLATE = (
    "-- %s\nnamespace Problem\ndef Target : Prop := (0 : Nat) <= 1\nend Problem\n"
)
ANSWER_TEMPLATE = (
    "import Bench.%s\nnamespace Submission\n"
    "theorem solution : Problem.Target := by\n"
    "  have key : (0 : Nat) <= 1 := Nat.zero_le 1\n"
    "  exact key\nend Submission\n"
)


def wide_proof(lines: int) -> str:
    return "by\n" + "".join(
        "  simp [Nat.add_comm, Nat.mul_comm] at h%d\n  omega\n" % i for i in range(lines)
    )


def chained_proof(steps: int) -> str:
    out = ["by", "  have h0 : True := trivial"]
    out += ["  have h%d : True := by exact h%d" % (i, i - 1) for i in range(1, steps)]
    out.append("  exact h%d" % (steps - 1))
    return "\n".join(out)


def best_of(fn: Callable[[], object], repeat: int) -> float:
    fn()
    return min(_once(fn) for _ in range(repeat))


def _once(fn: Callable[[], object]) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def build_cases() -> list[tuple[str, Callable[[], object], int]]:
    """(label, callable, repeat) for every measured case."""
    backend = create("mock")
    task = ProofTask(
        task_id="t", header="import Mathlib", formal_statement=TYPICAL_STATEMENT
    )
    attempt = ProofAttempt(task_id="t", proof=TYPICAL_PROOF + " MOCK_PASS")

    long_proof = wide_proof(250)
    chain_400 = chained_proof(400)
    chain_2000 = chained_proof(2000)
    honest_body = long_proof
    sorry_body = long_proof + "\n  sorry"

    # One raw verdict, reused: `measure` is what turns it into metrics, and
    # grading calls that once per answer.
    verdict = backend.verify(task, attempt)
    samples = {"t%d" % i: ["a", "a", "b", "c", "d"] for i in range(200)}

    # A benchmark the size of the real one: 100 problems, graded for
    # every participant, through all three stages.
    problem_src = PROBLEM_TEMPLATE
    answer_src = ANSWER_TEMPLATE
    problem_ids = ["P%03d" % i for i in range(1, 101)]
    problem_set = ProblemSet(problems={pid: problem_src % pid for pid in problem_ids})

    def submissions_for(n: int) -> list[Submission]:
        return [
            Submission(
                participant="p%02d" % k,
                answers={pid: answer_src % pid for pid in problem_ids},
            )
            for k in range(n)
        ]

    one_participant = submissions_for(1)
    twenty_participants = submissions_for(20)

    return [
        ("verify: one proof (no metrics)", lambda: backend.verify(task, attempt), 50),
        (
            "measure: one proof (all metrics)",
            lambda: measure(
                verdict,
                proof=attempt.proof,
                formal_statement=task.formal_statement,
            ),
            20,
        ),
        ("analyze_proof: 13KB proof", lambda: analyze_proof(long_proof), 10),
        ("analyze_proof: 400-step chain", lambda: analyze_proof(chain_400), 5),
        ("analyze_proof: 2000-step chain", lambda: analyze_proof(chain_2000), 3),
        (
            "screen_source: 13KB honest proof",
            lambda: screen_source(honest_body, "lean4", required_statement=None),
            10,
        ),
        (
            "screen_source: 13KB with a sorry",
            lambda: screen_source(sorry_body, "lean4", required_statement=None),
            10,
        ),
        ("extract_tactics: 13KB proof", lambda: extract_tactics(long_proof), 10),
        (
            "analyze_statement: typical statement",
            lambda: analyze_statement(TYPICAL_STATEMENT),
            50,
        ),
        ("sample_duplication: 1000 samples", lambda: sample_duplication(samples), 10),
        (
            "check_interface: one answer",
            lambda: check_interface(read_interface_problem(problem_src % "P001", module="Bench.P001"), answer_src % "P001"),
            20,
        ),
        (
            "grade: 100 problems x 1 participant",
            lambda: grade_contest(problem_set, one_participant, verifier=backend),
            1,
        ),
        (
            "grade: 100 problems x 20 participants",
            lambda: grade_contest(problem_set, twenty_participants, verifier=backend),
            1,
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    parser.add_argument(
        "--check", action="store_true", help="enforce the budgets and exit non-zero on a breach"
    )
    args = parser.parse_args(argv)

    measurements: dict[str, float] = {}
    for label, fn, repeat in build_cases():
        measurements[label] = best_of(fn, repeat)

    # The headline claim: the whole analysis layer is a rounding error next
    # to a real prover, so report it explicitly rather than leaving it to
    # be inferred from the table.
    per_attempt_ms = measurements["measure: one proof (all metrics)"] * 1000
    breaches = [
        (label, seconds, BUDGETS[label])
        for label, seconds in measurements.items()
        if label in BUDGETS and seconds > BUDGETS[label]
    ]

    if args.json:
        print(
            json.dumps(
                {
                    "platform": "%s %s / Python %s"
                    % (platform.system(), platform.machine(), platform.python_version()),
                    "per_attempt_ms": round(per_attempt_ms, 4),
                    "measurements_ms": {k: round(v * 1000, 4) for k, v in measurements.items()},
                    "budgets_ms": {k: v * 1000 for k, v in BUDGETS.items()},
                    "breaches": [
                        {"case": label, "ms": round(s * 1000, 3), "budget_ms": b * 1000}
                        for label, s, b in breaches
                    ],
                },
                indent=2,
            )
        )
    else:
        print(
            "%s %s / Python %s"
            % (platform.system(), platform.machine(), platform.python_version())
        )
        print("%-42s %10s %10s %8s" % ("case", "time", "budget", "of budget"))
        for label, seconds in measurements.items():
            budget = BUDGETS.get(label)
            print(
                "%-42s %9.3fms %9s %8s"
                % (
                    label,
                    seconds * 1000,
                    "%.0fms" % (budget * 1000) if budget else "-",
                    "%.1f%%" % (100 * seconds / budget) if budget else "-",
                )
            )
        print()
        print(
            "whole analysis layer: %.3f ms per attempt -- a 1000-attempt run spends "
            "%.2f s on metrics." % (per_attempt_ms, per_attempt_ms)
        )
        print(
            "For scale, one `lake env lean` compile is typically 1-60 s, so the "
            "prover dominates by 3-5 orders of magnitude."
        )

    if breaches:
        print("\nBUDGET BREACH:", file=sys.stderr)
        for label, seconds, budget in breaches:
            print(
                "  %-42s %.1fms > %.0fms budget" % (label, seconds * 1000, budget * 1000),
                file=sys.stderr,
            )
        print(
            "\nComplexity regressions are caught by tests/test_performance.py; these "
            "absolute bounds catch gross slowdowns. If the new cost is justified, "
            "update BUDGETS here and the table in docs/metrics.md.",
            file=sys.stderr,
        )
        return 1 if args.check else 0
    if args.check:
        print("\nall %d budgets met" % len(BUDGETS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
