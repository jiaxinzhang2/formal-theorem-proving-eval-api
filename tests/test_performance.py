"""Performance guarantees for the metrics layer.

The analysis layer runs on every attempt of every run, so a complexity
regression here is silently expensive. Two kinds of guard:

**Scaling tests** compare the cost at size n against size 4n and assert
the ratio is far below quadratic. These are the important ones: they are
machine-independent, so they mean the same thing on a fast desktop and a
throttled CI runner, and a complexity regression is the failure mode that
actually happens.

**Absolute budgets** are a backstop against a gross slowdown, with margins
wide enough (10-50x over measured) that ordinary machine variation cannot
trip them.

Timing uses ``min`` of several runs rather than mean: the minimum is the
least noisy estimator of true cost, since scheduler noise only ever adds.

There is also a correctness guard here, ``test_prefilter_...``, because the
reward-hacking screen was made ~4x faster by a literal prefilter and that
optimization is only safe if the prefilter never rules out a pattern that
would have matched.
"""

from __future__ import annotations

import time
from typing import Callable

import pytest

from ftp_eval import (
    ProofAttempt,
    ProofTask,
    analyze_proof,
    create,
    extract_tactics,
    screen_source,
    summarize,
)
from ftp_eval.analysis.structure import sample_duplication
from ftp_eval.soundness import PATTERNS, strip_comments

#: Ratio ceiling for a 4x size increase. Linear is 4, quadratic is 16.
#: 9 leaves generous room for constant-factor and cache effects while
#: still failing loudly on a quadratic regression.
SUBQUADRATIC = 9.0


def measure(fn: Callable[[], object], repeat: int = 5) -> float:
    """Best-of-N wall time in seconds."""
    fn()  # warm up caches and regex compilation
    return min(_timed(fn) for _ in range(repeat))


def _timed(fn: Callable[[], object]) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def assert_subquadratic(
    build: Callable[[int], object],
    run: Callable[[object], object],
    *,
    small: int,
    large: int,
    what: str,
) -> None:
    """Assert cost grows far slower than the square of the input size."""
    size_ratio = large / small
    small_input, large_input = build(small), build(large)
    small_time = measure(lambda: run(small_input))
    large_time = measure(lambda: run(large_input))
    if small_time <= 0:
        pytest.skip("timer resolution too coarse for %s" % what)
    ratio = large_time / small_time
    ceiling = SUBQUADRATIC * (size_ratio / 4.0)
    assert ratio < ceiling, (
        "%s scaled %.1fx for a %.0fx size increase (ceiling %.1fx). "
        "Quadratic would be ~%.0fx -- this looks like a complexity regression."
        % (what, ratio, size_ratio, ceiling, size_ratio**2)
    )


# -- input builders ----------------------------------------------------


def chained_proof(steps: int) -> str:
    """Every step cites the previous one: the dependency walk's worst case."""
    lines = ["by", "  have h0 : True := trivial"]
    lines += ["  have h%d : True := by exact h%d" % (i, i - 1) for i in range(1, steps)]
    lines.append("  exact h%d" % (steps - 1))
    return "\n".join(lines)


def wide_proof(lines: int) -> str:
    """Many independent tactic lines: the ordinary long-proof case."""
    return "by\n" + "".join(
        "  simp [Nat.add_comm, Nat.mul_comm] at h%d\n  omega\n" % i for i in range(lines)
    )


def repetitive_proof(lines: int) -> str:
    """Degenerate output: the repetition metrics' worst case."""
    return "by\n" + "  simp [foo]\n" * lines


# -- scaling guarantees ------------------------------------------------


def test_analyze_proof_is_subquadratic_in_dependency_chain_length():
    """The regression that already happened once.

    The first dependency walk regex-searched every earlier step name in
    every step's justification: 177ms at 400 steps, and quadratic.
    """
    assert_subquadratic(
        chained_proof, analyze_proof, small=150, large=600, what="analyze_proof (chained)"
    )


def test_analyze_proof_is_subquadratic_in_proof_length():
    assert_subquadratic(
        wide_proof, analyze_proof, small=150, large=600, what="analyze_proof (wide)"
    )


def test_repetition_metrics_are_subquadratic():
    assert_subquadratic(
        repetitive_proof, analyze_proof, small=200, large=800, what="analyze_proof (repetitive)"
    )


def test_tactic_extraction_is_subquadratic():
    assert_subquadratic(
        wide_proof, extract_tactics, small=200, large=800, what="extract_tactics"
    )


def test_reward_hacking_screen_is_subquadratic():
    assert_subquadratic(
        wide_proof,
        lambda source: screen_source(source, "lean4", required_statement=None),
        small=200,
        large=800,
        what="screen_source",
    )


def test_statement_screening_is_subquadratic_with_shadowing_checks_on():
    # The shadowing check intersects declared names against statement
    # identifiers, which must not become a per-name scan.
    statement = "theorem t (n : Nat) (h : Nat.Prime n) : n >= 2"
    assert_subquadratic(
        wide_proof,
        lambda source: screen_source(source, "lean4", required_statement=statement),
        small=200,
        large=800,
        what="screen_source (with statement)",
    )


def test_summarize_is_subquadratic_in_result_count():
    backend = create("mock")
    task = ProofTask(task_id="t", formal_statement="theorem t : True := by")
    template = backend.verify(task, ProofAttempt(task_id="t", proof=" simp MOCK_PASS"))

    def build(n: int) -> list:
        from dataclasses import replace

        return [
            replace(template, task_id="t%d" % (i // 5), attempt_id="a%d" % i, sample_index=i % 5)
            for i in range(n)
        ]

    assert_subquadratic(
        build, lambda results: summarize(results, ks=(1, 5)), small=500, large=2000, what="summarize"
    )


def test_sample_duplication_is_subquadratic():
    def build(n: int) -> dict:
        return {"t%d" % i: ["p%d" % (i % 3)] * 5 for i in range(n)}

    assert_subquadratic(
        build, sample_duplication, small=500, large=2000, what="sample_duplication"
    )


# -- absolute budgets (backstops, wide margins) -----------------------


def test_per_attempt_metric_cost_stays_small():
    """A whole `verify` on a typical proof, metrics included.

    Measured at ~0.16ms; the bound is 10ms, which is ~60x headroom and
    still three orders of magnitude under a real Lean compile.
    """
    backend = create("mock")
    task = ProofTask(
        task_id="t",
        header="import Mathlib",
        formal_statement="theorem t (n : Nat) (h : 0 < n) : n ^ 2 >= n := by",
    )
    attempt = ProofAttempt(
        task_id="t",
        proof=(
            " have k : 0 < n := h\n  have m : n * 1 <= n * n := Nat.mul_le_mul_left n h\n"
            "  nlinarith [k, m, sq_nonneg n] MOCK_PASS"
        ),
    )
    elapsed = measure(lambda: backend.verify(task, attempt), repeat=20)
    assert elapsed < 0.010, "verify took %.1fms per attempt" % (elapsed * 1000)


def test_a_thousand_attempt_run_is_analyzed_quickly():
    """End to end: 1000 attempts through verify plus summarize.

    Measured at ~0.19s; bounded at 10s. If this fails, the analysis layer
    has stopped being a rounding error next to the prover.
    """
    backend = create("mock")
    tasks = [
        ProofTask(task_id="t%d" % i, formal_statement="theorem t%d : True := by" % i)
        for i in range(200)
    ]
    proofs = [" simp MOCK_PASS", " omega MOCK_FAIL", " nlinarith MOCK_UNSOLVED"]

    started = time.perf_counter()
    results = [
        backend.verify(
            task,
            ProofAttempt(task_id=task.task_id, proof=proofs[s % len(proofs)], sample_index=s),
        )
        for task in tasks
        for s in range(5)
    ]
    summarize(results, ks=(1, 5))
    elapsed = time.perf_counter() - started
    assert len(results) == 1000
    assert elapsed < 10.0, "1000-attempt analysis took %.1fs" % elapsed


def test_a_pathological_proof_does_not_hang():
    """A degenerate model emitting thousands of steps must stay cheap."""
    proof = chained_proof(3000)
    elapsed = measure(lambda: analyze_proof(proof), repeat=2)
    assert elapsed < 2.0, "analyze_proof took %.2fs on 3000 steps" % elapsed


def test_screening_a_large_source_stays_cheap():
    source = wide_proof(1000)
    elapsed = measure(lambda: screen_source(source, "lean4", required_statement=None))
    assert elapsed < 0.5, "screen_source took %.1fms on %d chars" % (
        elapsed * 1000,
        len(source),
    )


# -- the prefilter must not change any verdict ------------------------


def test_every_pattern_matches_its_own_example():
    """The pattern table is self-testing.

    A pattern whose example it cannot catch is either a broken regex or a
    stale example, and both are silent failures of the screen.
    """
    broken = [
        p.id
        for p in PATTERNS
        if p.example and not p.pattern.search(strip_comments(p.example, p.languages[0]))
    ]
    assert not broken, "patterns that do not match their own example: %s" % broken


def test_prefilter_admits_every_pattern_example():
    """The speed optimization must never hide a real match.

    ``requires`` skips the regex when none of its literals are present. If
    a literal set were too narrow, the pattern would be silently switched
    off -- reward hacking would stop being detected and every test that
    checks a *positive* detection would still pass, because those supply
    the literal. This checks the implication directly: for every pattern,
    its own example must survive the prefilter.
    """
    missed = []
    for pattern in PATTERNS:
        if not (pattern.example and pattern.requires):
            continue
        lowered = strip_comments(pattern.example, pattern.languages[0]).lower()
        if not any(literal in lowered for literal in pattern.requires):
            missed.append((pattern.id, pattern.requires))
    assert not missed, (
        "these patterns' literal prefilters would skip their own example, so the "
        "pattern is effectively disabled: %s" % missed
    )


def test_prefilter_gives_the_same_verdicts_as_no_prefilter():
    """Differential test: prefiltered screening == unfiltered screening.

    Run over every pattern's example plus a set of honest proofs, so both
    directions are covered.
    """
    honest = [
        "theorem t (n : Nat) : n + 0 = n := by simp",
        "by\n  have k : 0 < n := h\n  nlinarith [k]",
        wide_proof(20),
        "-- a comment mentioning sorry and axiom\nby simp",
        "",
    ]
    corpus = [p.example for p in PATTERNS if p.example] + honest

    for source in corpus:
        for language in ("lean4", "coq", "isabelle", "axle"):
            body = strip_comments(source, language)
            lowered = body.lower()
            for pattern in PATTERNS:
                if language not in pattern.languages:
                    continue
                with_filter = pattern.search(body, lowered)
                without_filter = bool(pattern.pattern.search(body))
                assert with_filter == without_filter, (
                    "prefilter changed the verdict for %s on %r (filtered=%s, raw=%s)"
                    % (pattern.id, source[:60], with_filter, without_filter)
                )


def test_prefiltered_screen_is_faster_on_an_honest_proof():
    """The optimization has to actually pay, or it is only complexity.

    An honest proof triggers almost no literals, so the prefilter should
    rule out most patterns. Compared against running every regex
    unconditionally over the same body.
    """
    source = wide_proof(300)
    body = strip_comments(source, "lean4")
    lowered = body.lower()
    lean_patterns = [p for p in PATTERNS if "lean4" in p.languages]

    def with_prefilter() -> None:
        for p in lean_patterns:
            p.search(body, lowered)

    def without_prefilter() -> None:
        for p in lean_patterns:
            p.pattern.search(body)

    fast = measure(with_prefilter, repeat=10)
    slow = measure(without_prefilter, repeat=10)
    assert fast < slow, "the prefilter is not paying for itself (%.3fms vs %.3fms)" % (
        fast * 1000,
        slow * 1000,
    )
