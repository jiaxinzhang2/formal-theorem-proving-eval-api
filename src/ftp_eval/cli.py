"""CLI: grade frozen-target answer groups, audit original statements, and diagnose backends."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from . import __version__
from .autoformalization.checker import StatementChecker, format_statement_summary
from .autoformalization.types import StatementStatus
from .proving.running import grade_contest, load_problem_set, load_submissions
from .proving.running.problem_health import check_problem_health, format_health_summary
from .registry import available, available_judges, create, create_judge
from .jsonl import write_jsonl
from .backends.types import ProofAttempt, ProofTask, StatementTask, Status

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BACKEND_UNAVAILABLE = 3
EXIT_UNSOUND = 5


def _parse_options(pairs: Sequence[str]) -> dict[str, Any]:
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


def _open_judge(args: argparse.Namespace) -> Any:
    """Create the judge named by ``--judge``, or None. Never spends money silently."""
    if not args.judge:
        return None
    judge = create_judge(
        args.judge,
        consensus=args.judge_samples,
        recheck=args.judge_recheck,
        **_parse_options(args.judge_option),
    )
    info = judge.info()
    if not info.available:
        judge.close()
        print("judge %r is unavailable: %s" % (info.name, info.detail), file=sys.stderr)
        raise SystemExit(EXIT_BACKEND_UNAVAILABLE)
    if info.costs_money and not args.yes:
        judge.close()
        print(
            "judge %r calls a paid API (%s). Re-run with --yes to confirm."
            % (info.name, info.model),
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE)
    return judge


# -- the proving API: does this answer prove this theorem? -------------


def cmd_grade(args: argparse.Namespace) -> int:
    """Grade a benchmark: interface, kernel and axiom checks, then reporting."""
    problem_set = load_problem_set(args.problems)
    if not len(problem_set):
        print("no .lean problem files in %s" % args.problems, file=sys.stderr)
        return EXIT_USAGE
    submissions = load_submissions(args.submissions, problem_set)
    if not submissions:
        print("no participant directories in %s" % args.submissions, file=sys.stderr)
        return EXIT_USAGE

    verifier = None
    if args.backend:
        verifier = create(args.backend, **_parse_options(args.option))
        info = verifier.info()
        if not info.available:
            verifier.close()
            print(
                "backend %r is unavailable: %s\nFix the backend for kernel checks. "
                "Without a supported backend no answer counts as solved." % (info.name, info.detail),
                file=sys.stderr,
            )
            return EXIT_BACKEND_UNAVAILABLE

    print(
        "grading %d problem(s) x %d participant(s)%s"
        % (
            len(problem_set),
            len(submissions),
            "" if verifier else "   [kernel checks not run: no --backend]",
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
            run_metadata=_parse_options(args.run_metadata),
            strict_environment=args.strict_environment,
        )
    finally:
        if verifier is not None:
            verifier.close()

    print()
    print(result.format_text())

    if args.strict and any(not answer.solved for answer in result.grades()):
        return EXIT_UNSOUND
    return EXIT_OK


# -- the autoformalization API: is the problem set itself sound? -------


def cmd_audit(args: argparse.Namespace) -> int:
    """Check the setter's own problems before anyone answers them.

    A vacuous or trivially-true statement is the benchmark's bug, not a
    participant's. Worth finding once over the problem folder rather than
    inferring it later from a leaderboard on which everybody scored.
    """
    problem_set = load_problem_set(args.problems)
    if not len(problem_set):
        print("no .lean problem files in %s" % args.problems, file=sys.stderr)
        return EXIT_USAGE

    verifier = None
    if args.backend:
        verifier = create(args.backend, **_parse_options(args.option))
        if not verifier.info().available:
            # Unlike grading, an unavailable prover is not fatal here: the
            # faithfulness check needs no prover at all, and the health
            # probes report "not checked" rather than passing silently.
            print(
                "note: backend %r is unavailable, so the prover probes (elaborates, "
                "non-trivial, non-vacuous) will report as not checked" % args.backend,
                file=sys.stderr,
            )
    elif not args.quiet:
        print(
            "note: no --backend, so nothing will be probed for vacuity. A vacuously "
            "true problem is the worst kind to publish -- every answer to it is valid "
            "and none proves anything -- and only a prover can find one.",
            file=sys.stderr,
        )

    judge = _open_judge(args)
    language = problem_set.manifest.language or "lean4"
    # Two independent checks. Faithfulness is pure judge and needs no
    # prover; the health probes need a prover and never look at the prose.
    # Reported separately, because a setter needs both answers rather than
    # an average of them.
    checker = StatementChecker(judge, timeout_s=args.timeout)

    verdicts = []
    health = []
    without_prose = []
    try:
        for index, problem_id in enumerate(problem_set.ids(), start=1):
            prose = problem_set.prose_for(problem_id)
            if not prose:
                without_prose.append(problem_id)
            task = StatementTask(
                task_id=problem_id,
                informal_statement=prose,
                formal_statement=problem_set.source_for(problem_id),
                language=language,
            )
            verdict = checker.check(task)
            verdicts.append(verdict)
            interface = problem_set.manifest.raw.get("problems", {}).get(problem_id, {})
            report = check_problem_health(verifier, task, timeout_s=args.timeout,
                                          module=interface.get("module", ""),
                                          gold_arguments=interface.get("gold_arguments", ()))
            health.append(report)
            if not args.quiet:
                failures = verdict.failures or report.failures
                detail = "  <- %s" % failures[0].detail[:64] if failures else ""
                print(
                    "[%d/%d] %-13s %-11s %-14s%s"
                    % (
                        index,
                        len(problem_set),
                        verdict.status.value,
                        "UNGRADEABLE" if report.ungradeable else
                        ("suspect" if report.suspect
                         else ("gradeable" if report.probed else "not probed")),
                        problem_id[:14],
                        detail,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        if verifier is not None:
            verifier.close()
        if judge is not None:
            judge.close()

    if args.out:
        write_jsonl(
            args.out,
            [
                {"problem_id": v.task_id, "faithfulness": v.to_dict(), "health": h.to_dict()}
                for v, h in zip(verdicts, health, strict=True)
            ],
        )
        print("wrote %d verdict(s) to %s" % (len(verdicts), args.out), file=sys.stderr)

    print()
    print(format_statement_summary(verdicts))
    print()
    print(format_health_summary(health))

    # Two gaps in the problem set itself, reported apart from the verdicts
    # because they are the setter's homework, not a checker result.
    if without_prose:
        print()
        print(
            "%d problem(s) record no `prose:` metadata, so faithfulness was not "
            "judged for them: %s" % (len(without_prose), ", ".join(without_prose[:10]))
        )
    unsourced = problem_set.without_provenance()
    if unsourced:
        print(
            "%d problem(s) record no MathDB id and no source: %s"
            % (len(unsourced), ", ".join(unsourced[:10]))
        )

    if args.strict and (
        any(
            v.status is StatementStatus.UNFAITHFUL
            for v in verdicts
        )
        or any(h.ungradeable for h in health)
    ):
        return EXIT_UNSOUND
    return EXIT_OK


# -- diagnostics ------------------------------------------------------


def cmd_backends(args: argparse.Namespace) -> int:
    rows = []
    for name in available():
        try:
            rows.append(create(name, **_parse_options(args.option)).info().to_dict())
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


def cmd_doctor(args: argparse.Namespace) -> int:
    backend = create(args.backend, **_parse_options(args.option))
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
        # A backend that claims to be available but cannot tell a good proof
        # from a bad one is worse than one that admits it is broken, so the
        # smoke test checks both directions.
        print("\nsmoke test:")
        good = backend.verify(
            ProofTask(task_id="smoke_ok", formal_statement="MOCK_PASS", language=info.language),
            ProofAttempt(task_id="smoke_ok", proof="MOCK_PASS"),
        )
        bad = backend.verify(
            ProofTask(task_id="smoke_bad", formal_statement="MOCK_FAIL", language=info.language),
            ProofAttempt(task_id="smoke_bad", proof="MOCK_FAIL"),
        )
        print("  accepts a passing fixture: %s" % good.status.value)
        print("  rejects a failing fixture: %s" % bad.status.value)
        if good.status is not Status.VERIFIED or bad.status is Status.VERIFIED:
            print(
                "  these fixtures only mean something to the mock backend; for a real "
                "prover, use a known-good and a known-bad frozen benchmark instead.",
                file=sys.stderr,
            )
    return EXIT_OK


# -- parser -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ftp-eval",
        description="Evaluate formal theorem proving from Lean files.",
    )
    parser.add_argument("--version", action="version", version="ftp-eval %s" % __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_backend(p: argparse.ArgumentParser, *, help: str) -> None:
        p.add_argument("-b", "--backend", help=help)
        p.add_argument(
            "-o",
            "--option",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="backend option, JSON-decoded when possible (repeatable)",
        )

    # -- proving ------------------------------------------------------
    p_grade = sub.add_parser(
        "grade", help="grade a benchmark: N Lean problems x many participants"
    )
    p_grade.add_argument(
        "--problems", required=True, help="directory of problem .lean files (stem = problem id)"
    )
    p_grade.add_argument(
        "--submissions",
        required=True,
        help="directory with one subdirectory per participant, files named after problems",
    )
    add_backend(
        p_grade,
        help="prover for frozen-module builds; omitted means no kernel verdict and zero solved",
    )
    p_grade.add_argument(
        "--out", default="runs", help="results root (default: runs; one subdirectory per run)"
    )
    p_grade.add_argument("--strict-environment", action="store_true",
                         help="reject missing or mismatched exact toolchain/dependency pins before grading")
    p_grade.add_argument("--run-id", help="name the run directory (default: a UTC timestamp)")
    p_grade.add_argument("--timeout", type=float, default=300.0, help="seconds per compile")
    p_grade.add_argument("--quiet", action="store_true")
    p_grade.add_argument(
        "--strict", action="store_true", help="exit non-zero if any answer was refused"
    )
    p_grade.add_argument("--run-metadata", action="append", default=[], metavar="KEY=VALUE", help="experiment labels recorded in run.json (repeatable)")
    p_grade.set_defaults(func=cmd_grade)

    # -- autoformalization --------------------------------------------
    p_audit = sub.add_parser(
        "audit",
        help="check the problem set itself: faithful to its prose (--judge), and "
        "gradeable at all (--backend)",
    )
    p_audit.add_argument("--problems", required=True, help="directory of problem .lean files")
    add_backend(
        p_audit,
        help="prover for the health probes: elaborates, non-trivial, non-vacuous. "
        "Omitted means vacuity is never checked, and only a prover can check it",
    )
    p_audit.add_argument("--judge", help="faithfulness judge (mock, claude)")
    p_audit.add_argument(
        "--judge-option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="judge option, e.g. model=claude-opus-5 (repeatable)",
    )
    p_audit.add_argument(
        "--judge-samples",
        type=int,
        default=1,
        help="draw N samples and take a majority vote (>1 enables consensus)",
    )
    p_audit.add_argument(
        "--judge-recheck",
        type=int,
        default=2,
        help="extra samples drawn before a rejection stands (default 2)",
    )
    p_audit.add_argument("--yes", action="store_true", help="confirm a paid judge may run")
    p_audit.add_argument("--out", help="write verdicts here as JSONL")
    p_audit.add_argument("--timeout", type=float, default=120.0)
    p_audit.add_argument("--quiet", action="store_true")
    p_audit.add_argument("--strict", action="store_true", help="exit non-zero on a bad statement")
    p_audit.set_defaults(func=cmd_audit)

    # -- diagnostics --------------------------------------------------
    p_backends = sub.add_parser("backends", help="list provers and their availability")
    p_backends.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE")
    p_backends.add_argument("--json", action="store_true")
    p_backends.set_defaults(func=cmd_backends)

    p_judges = sub.add_parser("judges", help="list faithfulness judges, and which cost money")
    p_judges.add_argument("--json", action="store_true")
    p_judges.set_defaults(func=cmd_judges)

    p_doctor = sub.add_parser("doctor", help="diagnose one backend")
    p_doctor.add_argument("-b", "--backend", default="mock")
    p_doctor.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE")
    p_doctor.add_argument("--smoke", action="store_true", help="also run a pass/fail fixture pair")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def _force_utf8_output() -> None:
    """Print Lean source without depending on the console codepage.

    Lean is full of characters a Windows console cannot encode by default
    (a bare `ℕ` is enough), and a UnicodeEncodeError while printing a
    probe looks like a tool failure rather than a terminal setting. Errors
    are replaced rather than raised: a mangled character is a far smaller
    problem than losing the verdict that was about to be reported.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - a detached stream
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    except (KeyError, ValueError, OSError) as exc:
        # Configuration and data problems get a one-line message; a real
        # bug still gets its traceback.
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
