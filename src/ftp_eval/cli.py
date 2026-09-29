"""Command line interface: ``ftp-eval``.

Subcommands
-----------
``backends``  list backends and whether each can run here
``doctor``    diagnose one backend in detail, with an exit code CI can use
``verify``    run a task set against a backend, streaming results to JSONL
``score``     turn a results file into pass@k and a breakdown
``compare``   rank several results files side by side
``preview``   print the exact source a backend would be handed

Only argparse is used, so the CLI works in a bare virtualenv.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .dataset import load_attempts, load_results, load_tasks, load_triplets, write_jsonl
from .proving.analysis.scoring import compare as compare_summaries, summarize
from .end_to_end import EndToEndRunner, EndToEndStatus, format_end_to_end
from .registry import available, available_judges, create, create_judge
from .proving.runner import EvalRunner, ProgressEvent, RunConfig
from .autoformalization.checker import StatementChecker, format_statement_summary
from .proving.matching import SubmissionMatcher, match_submission
from .proving.grading import grade_contest, load_problem_set, load_submissions
from .types import ProofAttempt, ProofTask, StatementStatus, Status
from .proving.verifier import assemble_source, screen_soundness

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BACKEND_UNAVAILABLE = 3
EXIT_RUN_HAD_ERRORS = 4
EXIT_UNSOUND = 5


def _parse_backend_config(pairs: Sequence[str]) -> dict[str, Any]:
    """Parse ``-o key=value`` options, JSON-decoding values when possible."""
    config: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit("bad -o option %r, expected key=value" % pair)
        key, _, value = pair.partition("=")
        try:
            config[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            config[key.strip()] = value
    return config


def _parse_ks(text: str) -> list[int]:
    ks = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            k = int(part)
        except ValueError as exc:
            raise SystemExit("bad --k value %r, expected integers like 1,5,10" % part) from exc
        if k < 1:
            raise SystemExit("--k values must be >= 1")
        ks.append(k)
    return ks or [1]


# -- subcommands ------------------------------------------------------


def cmd_backends(args: argparse.Namespace) -> int:
    rows = []
    for name in available():
        try:
            info = create(name, **_parse_backend_config(args.option)).info()
            rows.append(info.to_dict())
        except Exception as exc:
            rows.append(
                {
                    "name": name,
                    "language": "?",
                    "available": False,
                    "version": None,
                    "detail": "%s: %s" % (type(exc).__name__, exc),
                    "supports": [],
                }
            )
    if args.json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    print("%-10s %-10s %-11s %s" % ("backend", "language", "available", "detail"))
    for row in rows:
        print(
            "%-10s %-10s %-11s %s"
            % (
                row["name"],
                row["language"],
                "yes" if row["available"] else "NO",
                row.get("version") or row.get("detail") or "",
            )
        )
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    backend = create(args.backend, **_parse_backend_config(args.option))
    info = backend.info()
    print("backend:   %s" % info.name)
    print("language:  %s" % info.language)
    print("available: %s" % ("yes" if info.available else "NO"))
    if info.version:
        print("version:   %s" % info.version)
    if info.detail:
        print("detail:    %s" % info.detail)
    if info.supports:
        print("supports:  %s" % ", ".join(info.supports))
    if not info.available:
        print("\nThis backend cannot run here. Fix the detail above and re-run.", file=sys.stderr)
        return EXIT_BACKEND_UNAVAILABLE

    if args.smoke:
        # A backend that says it is available but cannot tell a good proof
        # from a bad one is worse than one that says it is broken, so the
        # smoke test checks both directions.
        print("\nsmoke test:")
        good = ProofTask(
            task_id="smoke_ok",
            header="",
            formal_statement="MOCK_PASS",
            language=info.language,
        )
        good_result = backend.verify(good, ProofAttempt(task_id="smoke_ok", proof="MOCK_PASS"))
        bad_result = backend.verify(
            ProofTask(task_id="smoke_bad", formal_statement="MOCK_FAIL", language=info.language),
            ProofAttempt(task_id="smoke_bad", proof="MOCK_FAIL"),
        )
        print("  accepts a passing fixture: %s" % good_result.status.value)
        print("  rejects a failing fixture: %s" % bad_result.status.value)
        if good_result.status is not Status.VERIFIED or bad_result.status is Status.VERIFIED:
            print(
                "  smoke test is only meaningful for the mock backend; for a real prover "
                "supply your own known-good and known-bad fixtures with `verify`.",
                file=sys.stderr,
            )
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    backend = create(args.backend, **_parse_backend_config(args.option))
    info = backend.info()
    if not info.available:
        print("backend %r is unavailable: %s" % (info.name, info.detail), file=sys.stderr)
        return EXIT_BACKEND_UNAVAILABLE

    tasks = load_tasks(args.tasks, language=args.language)
    attempts = load_attempts(args.attempts)
    if args.task_id:
        wanted = set(args.task_id)
        tasks = [t for t in tasks if t.task_id in wanted]
        attempts = [a for a in attempts if a.task_id in wanted]
    if args.limit:
        tasks = tasks[: args.limit]
        keep = {t.task_id for t in tasks}
        attempts = [a for a in attempts if a.task_id in keep]
    if not tasks:
        print("no tasks to run after filtering", file=sys.stderr)
        return EXIT_USAGE

    config = RunConfig(
        timeout_s=args.timeout,
        concurrency=args.concurrency,
        cache_dir=args.cache_dir,
        resume=args.resume,
        limit_samples=args.limit_samples,
    )

    quiet = args.quiet

    def on_progress(event: ProgressEvent) -> None:
        if not quiet:
            # Straight to stderr, unbuffered-ish: a two-hour run should
            # show what it is doing while it does it.
            print(event.format_line(), file=sys.stderr, flush=True)

    runner = EvalRunner(backend, config, on_progress=on_progress)
    try:
        results = runner.run(tasks, attempts, out_path=args.out)
    finally:
        backend.close()

    summary = summarize(results, ks=_parse_ks(args.k), include_per_task=bool(args.per_task_out))
    print()
    print(summary.format_text(include_tactics=args.tactics))

    if args.summary_out:
        Path(args.summary_out).write_text(
            json.dumps(summary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print("summary written to %s" % args.summary_out, file=sys.stderr)
    if args.per_task_out:
        Path(args.per_task_out).write_text(
            "\n".join(json.dumps(o.to_dict(), ensure_ascii=False) for o in summary.per_task) + "\n",
            encoding="utf-8",
        )
        print("per-task outcomes written to %s" % args.per_task_out, file=sys.stderr)

    if summary.rejected and args.strict:
        return EXIT_UNSOUND
    if summary.errored and args.strict:
        return EXIT_RUN_HAD_ERRORS
    return EXIT_OK


def cmd_judges(args: argparse.Namespace) -> int:
    rows = []
    for name in available_judges():
        try:
            rows.append(create_judge(name).info().to_dict())
        except Exception as exc:
            rows.append(
                {
                    "name": name,
                    "available": False,
                    "model": None,
                    "detail": "%s: %s" % (type(exc).__name__, exc),
                    "costs_money": None,
                }
            )
    if args.json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    print("%-10s %-11s %-7s %-18s %s" % ("judge", "available", "paid", "model", "detail"))
    for row in rows:
        print(
            "%-10s %-11s %-7s %-18s %s"
            % (
                row["name"],
                "yes" if row["available"] else "NO",
                "yes" if row["costs_money"] else "no",
                row.get("model") or "",
                (row.get("detail") or "")[:70],
            )
        )
    return EXIT_OK


def _build_checker(args: argparse.Namespace) -> tuple[Any, Any]:
    """Build the verifier and judge a statement check needs."""
    verifier = None
    if args.backend:
        verifier = create(args.backend, **_parse_backend_config(args.option))
        info = verifier.info()
        if not info.available:
            print(
                "note: backend %r is unavailable (%s); prover-decidable checks will "
                "report as not run" % (info.name, info.detail),
                file=sys.stderr,
            )
    judge = None
    if args.judge:
        judge = create_judge(
            args.judge,
            consensus=args.judge_samples,
            recheck=args.judge_recheck,
            **_parse_backend_config(args.judge_option),
        )
        judge_info = judge.info()
        if not judge_info.available:
            print(
                "judge %r is unavailable: %s" % (judge_info.name, judge_info.detail),
                file=sys.stderr,
            )
            raise SystemExit(EXIT_BACKEND_UNAVAILABLE)
        if judge_info.costs_money and not args.yes:
            print(
                "judge %r calls a paid API (%s). Re-run with --yes to confirm."
                % (judge_info.name, judge_info.model),
                file=sys.stderr,
            )
            raise SystemExit(EXIT_USAGE)
    return verifier, judge


def cmd_check_statement(args: argparse.Namespace) -> int:
    verifier, judge = _build_checker(args)
    if not args.informal and judge is not None:
        print(
            "note: no --informal supplied, so there is no prose to judge faithfulness "
            "against; the judge will be skipped and only structural checks run",
            file=sys.stderr,
        )
        judge.close()
        judge = None
    tasks, _ = load_triplets(
        args.informal, args.formal, None, language=args.language or "lean4"
    )
    if args.limit:
        tasks = tasks[: args.limit]

    checker = StatementChecker(verifier, judge, timeout_s=args.timeout)
    verdicts = []
    try:
        for index, task in enumerate(tasks, start=1):
            verdict = checker.check(task)
            verdicts.append(verdict)
            if not args.quiet:
                failures = verdict.failures
                detail = "  <- %s" % failures[0].detail[:80] if failures else ""
                print(
                    "[%d/%d] %-12s %-28s%s"
                    % (index, len(tasks), verdict.status.value, task.task_id[:28], detail),
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        if verifier is not None:
            verifier.close()
        if judge is not None:
            judge.close()

    if args.out:
        write_jsonl(args.out, verdicts)
        print("wrote %d verdict(s) to %s" % (len(verdicts), args.out), file=sys.stderr)
    print()
    print(format_statement_summary(verdicts))

    if args.strict and any(
        v.status in (StatementStatus.MALFORMED, StatementStatus.SUSPICIOUS) for v in verdicts
    ):
        return EXIT_UNSOUND
    return EXIT_OK


def cmd_eval_all(args: argparse.Namespace) -> int:
    """Statement check and proof check together, with a combined verdict."""
    verifier, judge = _build_checker(args)
    if verifier is None:
        print("eval-all needs a verifier; pass --backend", file=sys.stderr)
        return EXIT_USAGE
    info = verifier.info()
    if not info.available:
        print("backend %r is unavailable: %s" % (info.name, info.detail), file=sys.stderr)
        return EXIT_BACKEND_UNAVAILABLE

    tasks, attempts = load_triplets(
        args.informal, args.formal, args.proofs, language=args.language or "lean4"
    )
    if args.limit:
        tasks = tasks[: args.limit]
        keep = {t.task_id for t in tasks}
        attempts = [a for a in attempts if a.task_id in keep]

    checker = StatementChecker(verifier, judge, timeout_s=args.timeout)
    runner = EndToEndRunner(verifier, checker, timeout_s=args.timeout)
    results = []
    try:
        for index, result in enumerate(runner.iter_run(tasks, attempts), start=1):
            results.append(result)
            if not args.quiet:
                print(
                    "[%d/%d] %-26s %s"
                    % (index, len(tasks), result.status.value, result.task_id[:30]),
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        verifier.close()
        if judge is not None:
            judge.close()

    if args.out:
        write_jsonl(args.out, results)
        print("wrote %d result(s) to %s" % (len(results), args.out), file=sys.stderr)

    print()
    print(format_end_to_end(results))
    print()
    print(format_statement_summary([r.statement for r in results]))
    proof_results = [r.proof for r in results if r.proof is not None]
    if proof_results:
        print()
        print(summarize(proof_results, ks=(1,)).format_text(include_tactics=args.tactics))

    if args.strict and not all(r.solved for r in results):
        failed = [r for r in results if r.status is not EndToEndStatus.SOLVED]
        return EXIT_UNSOUND if any(
            r.status
            in (EndToEndStatus.PROVED_WRONG_STATEMENT, EndToEndStatus.HACKED_PROOF)
            for r in failed
        ) else EXIT_OK
    return EXIT_OK


def cmd_match(args: argparse.Namespace) -> int:
    """Does answer.lean answer theorem.lean?

    The input contract: two Lean files, one theorem per problem file. Not
    "does the answer compile" -- a file can compile perfectly and have
    quietly restated the theorem.
    """
    problem_source = Path(args.theorem).read_text(encoding="utf-8")
    reports = []
    for answer_path in args.answer:
        answer_source = Path(answer_path).read_text(encoding="utf-8")
        report = match_submission(
            problem_source,
            answer_source,
            target=args.target,
            require_proof=not args.allow_unproved,
        )
        reports.append((answer_path, report))
        if not args.quiet:
            print("== %s" % answer_path)
            print(report.format_text())
            print()

    if args.show_probe:
        # The prover-backed confirmation: state the problem's theorem and
        # close it with the submission's proof term. Printed rather than
        # compiled, since compiling needs a Lean project.
        path, _ = reports[0]
        probe = SubmissionMatcher().build_confirmation_source(
            problem_source,
            Path(path).read_text(encoding="utf-8"),
            target=args.target,
        )
        print("== prover confirmation source for %s" % path)
        print(probe if probe else "(could not be assembled; read that as NOT confirmed)")
        print()

    if args.out:
        write_jsonl(
            args.out,
            [{"answer_file": path, **report.to_dict()} for path, report in reports],
        )
        print("wrote %d verdict(s) to %s" % (len(reports), args.out), file=sys.stderr)

    counts: dict[str, int] = {}
    for _, report in reports:
        counts[report.status.value] = counts.get(report.status.value, 0) + 1
    print("summary: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))

    if args.strict and any(
        not report.status.answers_the_problem for _, report in reports
    ):
        return EXIT_UNSOUND
    return EXIT_OK


def cmd_grade(args: argparse.Namespace) -> int:
    """Grade a benchmark: N problems, many participants, three stages."""
    problem_set = load_problem_set(args.problems)
    submissions = load_submissions(args.submissions, problem_set)
    if not submissions:
        print("no participant directories in %s" % args.submissions, file=sys.stderr)
        return EXIT_USAGE

    verifier = None
    if args.backend:
        verifier = create(args.backend, **_parse_backend_config(args.option))
        info = verifier.info()
        if not info.available:
            print(
                "backend %r is unavailable: %s\nStage 2 would be skipped, so `solved` "
                "would mean only that the text screen passed. Re-run without -b to "
                "accept that explicitly, or fix the backend."
                % (info.name, info.detail),
                file=sys.stderr,
            )
            return EXIT_BACKEND_UNAVAILABLE

    print(
        "grading %d problem(s) x %d participant(s)%s"
        % (
            len(problem_set),
            len(submissions),
            "" if verifier else "   [stage 2 skipped: no --backend]",
        ),
        file=sys.stderr,
    )

    quiet = args.quiet

    def on_grade(graded: Any) -> None:
        if not quiet:
            print(
                "  %-16s %s" % (graded.participant[:16], graded.format_text()),
                file=sys.stderr,
                flush=True,
            )

    try:
        result = grade_contest(
            problem_set,
            submissions,
            verifier=verifier,
            timeout_s=args.timeout,
            output_dir=args.out,
            run_id=args.run_id,
            on_grade=on_grade,
        )
    finally:
        if verifier is not None:
            verifier.close()

    print()
    print(result.format_text())

    if args.strict and result.statistics.refused_at:
        return EXIT_UNSOUND
    return EXIT_OK


def cmd_score(args: argparse.Namespace) -> int:
    results = load_results(args.results)
    summary = summarize(results, ks=_parse_ks(args.k), include_per_task=args.unsolved)
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(summary.format_text(include_tactics=args.tactics))
        if args.unsolved:
            unsolved = [o for o in summary.per_task if not o.solved]
            if unsolved:
                print("\nunsolved tasks (%d):" % len(unsolved))
                for o in unsolved:
                    print(
                        "  %-30s samples=%-3d %s"
                        % (o.task_id, o.samples, o.first_error_kind.value if o.first_error_kind else "")
                    )
    if args.strict and not summary.integrity_ok:
        return EXIT_UNSOUND if summary.rejected else EXIT_RUN_HAD_ERRORS
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    summaries = {}
    ks = _parse_ks(args.k)
    for spec in args.results:
        name, _, path = spec.partition("=")
        if not path:
            name, path = Path(spec).stem, spec
        summaries[name] = summarize(load_results(path), ks=ks)
    print(compare_summaries(summaries, k=ks[0]))
    return EXIT_OK


def cmd_preview(args: argparse.Namespace) -> int:
    tasks = {t.task_id: t for t in load_tasks(args.tasks, language=args.language)}
    attempts = load_attempts(args.attempts)
    shown = 0
    for attempt in attempts:
        if args.task_id and attempt.task_id not in set(args.task_id):
            continue
        task = tasks.get(attempt.task_id)
        if task is None:
            continue
        source = assemble_source(task, attempt)
        report = screen_soundness(task, attempt, source)
        print("=" * 72)
        print("# %s (%s, assembly=%s)" % (attempt.attempt_id, task.language, task.assembly.value))
        if not report.ok:
            print("# SOUNDNESS: " + "; ".join(report.violations))
        print("=" * 72)
        print(source)
        shown += 1
        if args.limit and shown >= args.limit:
            break
    if not shown:
        print("nothing matched", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


# -- parser -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ftp-eval",
        description="Evaluate formal theorem-proving models through one verifier API.",
    )
    parser.add_argument("--version", action="version", version="ftp-eval %s" % __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_backend_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("-b", "--backend", default="mock", help="backend name (default: mock)")
        p.add_argument(
            "-o",
            "--option",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="backend option; values are JSON-decoded when possible (repeatable)",
        )

    p_backends = sub.add_parser("backends", help="list backends and their availability")
    p_backends.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE")
    p_backends.add_argument("--json", action="store_true")
    p_backends.set_defaults(func=cmd_backends)

    p_doctor = sub.add_parser("doctor", help="diagnose one backend")
    add_backend_opts(p_doctor)
    p_doctor.add_argument("--smoke", action="store_true", help="also run a pass/fail fixture pair")
    p_doctor.set_defaults(func=cmd_doctor)

    p_verify = sub.add_parser("verify", help="verify attempts against tasks")
    add_backend_opts(p_verify)
    p_verify.add_argument("--tasks", required=True, help="tasks .jsonl/.json")
    p_verify.add_argument("--attempts", required=True, help="attempts .jsonl/.json")
    p_verify.add_argument("--out", help="write results here as JSONL (streamed)")
    p_verify.add_argument("--summary-out", help="write the summary here as JSON")
    p_verify.add_argument("--per-task-out", help="write per-task outcomes here as JSONL")
    p_verify.add_argument("--timeout", type=float, default=300.0, help="seconds per attempt")
    p_verify.add_argument("-j", "--concurrency", type=int, default=1)
    p_verify.add_argument("--cache-dir", help="reuse verdicts for identical (task, proof) pairs")
    p_verify.add_argument("--resume", action="store_true", help="skip attempts already in --out")
    p_verify.add_argument("--language", help="language for task records that omit it")
    p_verify.add_argument("--task-id", action="append", help="only these task ids (repeatable)")
    p_verify.add_argument("--limit", type=int, help="only the first N tasks")
    p_verify.add_argument("--limit-samples", type=int, help="only the first N samples per task")
    p_verify.add_argument("--k", default="1", help="pass@k values, e.g. 1,5,10")
    p_verify.add_argument(
        "--tactics",
        action="store_true",
        help="also show tactic frequencies and proof-structure metrics",
    )
    p_verify.add_argument("--quiet", action="store_true", help="no per-attempt progress lines")
    p_verify.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any attempt was unsound or errored",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_score = sub.add_parser("score", help="score an existing results file")
    p_score.add_argument("results")
    p_score.add_argument("--k", default="1,5,10")
    p_score.add_argument("--json", action="store_true")
    p_score.add_argument("--unsolved", action="store_true", help="also list unsolved tasks")
    p_score.add_argument(
        "--tactics",
        action="store_true",
        help="also show tactic frequencies and proof-structure metrics",
    )
    p_score.add_argument("--strict", action="store_true")
    p_score.set_defaults(func=cmd_score)

    p_match = sub.add_parser(
        "match",
        help="check whether a submitted Lean file answers the problem's Lean file",
    )
    p_match.add_argument("theorem", help="the problem file, e.g. theorem.lean")
    p_match.add_argument(
        "answer", nargs="+", help="the submitted file(s), e.g. answer.lean"
    )
    p_match.add_argument(
        "--target",
        help="theorem name under test. Not needed for the one-problem-per-file "
        "convention; supply it only when a file holds several statements",
    )
    p_match.add_argument(
        "--allow-unproved",
        action="store_true",
        help="do not require a proof, i.e. only check that the statement matches",
    )
    p_match.add_argument(
        "--show-probe",
        action="store_true",
        help="print the prover-backed confirmation source for the first submission: "
        "the problem's theorem closed with the submission's proof term",
    )
    p_match.add_argument("--out", help="write verdicts here as JSONL")
    p_match.add_argument("--quiet", action="store_true")
    p_match.add_argument(
        "--strict", action="store_true", help="exit non-zero unless every submission matches"
    )
    p_match.set_defaults(func=cmd_match)

    p_grade = sub.add_parser(
        "grade",
        help="grade a benchmark: N Lean problems x many participants, three stages",
    )
    p_grade.add_argument(
        "--problems", required=True, help="directory of problem .lean files (stem = problem id)"
    )
    p_grade.add_argument(
        "--submissions",
        required=True,
        help="directory with one subdirectory per participant, files named after problems",
    )
    p_grade.add_argument(
        "-b",
        "--backend",
        help="prover for stage 2. Omitted means stage 2 is skipped and `solved` "
        "reflects the text screen only",
    )
    p_grade.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE")
    p_grade.add_argument(
        "--out", help="write the results directory under here (one subdirectory per run)"
    )
    p_grade.add_argument("--run-id", help="name the run directory (default: a UTC timestamp)")
    p_grade.add_argument("--timeout", type=float, default=300.0, help="seconds per compile")
    p_grade.add_argument("--quiet", action="store_true")
    p_grade.add_argument(
        "--strict", action="store_true", help="exit non-zero if any answer was refused"
    )
    p_grade.set_defaults(func=cmd_grade)

    p_judges = sub.add_parser("judges", help="list faithfulness judges")
    p_judges.add_argument("--json", action="store_true")
    p_judges.set_defaults(func=cmd_judges)

    def add_judge_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--judge", help="faithfulness judge (mock, claude)")
        p.add_argument(
            "--judge-option",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="judge option, e.g. model=claude-opus-5 (repeatable)",
        )
        p.add_argument(
            "--judge-samples",
            type=int,
            default=1,
            help="draw N samples and take a majority vote (>1 enables consensus)",
        )
        p.add_argument(
            "--judge-recheck",
            type=int,
            default=2,
            help="extra samples drawn before a rejection stands (default 2)",
        )
        p.add_argument(
            "--yes",
            action="store_true",
            help="confirm that a paid judge may be called",
        )

    p_check = sub.add_parser(
        "check-statement",
        help="check formalizations against their natural-language problems",
    )
    p_check.add_argument(
        "--informal",
        help="natural-language problems .jsonl. Omit when the statements are GIVEN "
        "(the harness supplies them): there is then no prose to judge faithfulness "
        "against, and the structural checks run alone",
    )
    p_check.add_argument("--formal", required=True, help="formal statements .jsonl")
    p_check.add_argument("-b", "--backend", default="lean4", help="prover for the probes")
    p_check.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE")
    add_judge_opts(p_check)
    p_check.add_argument("--out", help="write verdicts here as JSONL")
    p_check.add_argument("--timeout", type=float, default=120.0)
    p_check.add_argument("--language")
    p_check.add_argument("--limit", type=int)
    p_check.add_argument("--quiet", action="store_true")
    p_check.add_argument(
        "--strict", action="store_true", help="exit non-zero on a bad formalization"
    )
    p_check.set_defaults(func=cmd_check_statement)

    p_all = sub.add_parser(
        "eval-all",
        help="check formalization and proof together, from the three-file layout",
    )
    p_all.add_argument("--informal", required=True)
    p_all.add_argument("--formal", required=True)
    p_all.add_argument("--proofs", required=True)
    p_all.add_argument("-b", "--backend", default="lean4")
    p_all.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE")
    add_judge_opts(p_all)
    p_all.add_argument("--out", help="write combined results here as JSONL")
    p_all.add_argument("--timeout", type=float, default=300.0)
    p_all.add_argument("--language")
    p_all.add_argument("--limit", type=int)
    p_all.add_argument("--quiet", action="store_true")
    p_all.add_argument("--tactics", action="store_true")
    p_all.add_argument("--strict", action="store_true")
    p_all.set_defaults(func=cmd_eval_all)

    p_compare = sub.add_parser("compare", help="rank several results files")
    p_compare.add_argument("results", nargs="+", metavar="[NAME=]PATH")
    p_compare.add_argument("--k", default="1")
    p_compare.set_defaults(func=cmd_compare)

    p_preview = sub.add_parser("preview", help="print the assembled source, without verifying")
    p_preview.add_argument("--tasks", required=True)
    p_preview.add_argument("--attempts", required=True)
    p_preview.add_argument("--task-id", action="append")
    p_preview.add_argument("--language")
    p_preview.add_argument("--limit", type=int, default=3)
    p_preview.set_defaults(func=cmd_preview)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (KeyError, ValueError, OSError) as exc:
        # Configuration and data problems get a one-line message; a real
        # bug still gets its traceback.
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
