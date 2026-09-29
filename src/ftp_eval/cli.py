"""Command line interface: ``ftp-eval``.

One input format throughout: **Lean files**. A problem is one ``.lean``
file with one theorem, an answer is another, a benchmark is a folder of
problems.

Grading answers -- the proving API::

    ftp-eval match theorem.lean answer.lean          one answer, one problem
    ftp-eval grade --problems P/ --submissions S/    a whole benchmark

Checking the problem set itself, before anyone answers it -- the
autoformalization API::

    ftp-eval audit --problems P/                     are the statements sound?

Diagnostics::

    ftp-eval backends          which provers can run here
    ftp-eval judges            which judges can run here, and which cost money
    ftp-eval doctor -b lean4   why a backend cannot run

Only argparse is used, so the CLI works in a bare virtualenv.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .autoformalization.checker import StatementChecker, format_statement_summary
from .autoformalization.types import StatementStatus
from .proving.grading import grade_contest, load_problem_set, load_submissions
from .proving.matching import SubmissionMatcher
from .registry import available, available_judges, create, create_judge
from .io import write_jsonl
from .shared.types import ProofAttempt, ProofTask, StatementTask, Status

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


def cmd_match(args: argparse.Namespace) -> int:
    """Not whether answer.lean compiles.

    A file can compile perfectly having quietly restated the theorem into
    something easier, so the question is whether it proves *this* one.
    """
    problem_source = Path(args.theorem).read_text(encoding="utf-8")

    verifier = None
    if args.backend:
        verifier = create(args.backend, **_parse_options(args.option))
        info = verifier.info()
        if not info.available:
            verifier.close()
            print("backend %r is unavailable: %s" % (info.name, info.detail), file=sys.stderr)
            return EXIT_BACKEND_UNAVAILABLE

    matcher = SubmissionMatcher(verifier, timeout_s=args.timeout)
    reports = []
    try:
        for answer_path in args.answer:
            answer_source = Path(answer_path).read_text(encoding="utf-8")
            report = matcher.check(
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
            # The source a prover is handed: the problem's proposition,
            # closed with the answer's proof term, over the problem's own
            # definitions. Printed rather than compiled, because compiling
            # needs a Lean project.
            path = reports[0][0]
            probe = matcher.build_confirmation_source(
                problem_source,
                Path(path).read_text(encoding="utf-8"),
                target=args.target,
            )
            print("== the source a prover would be given, for %s" % path)
            print(probe if probe else "(could not be assembled; read that as NOT confirmed)")
            print()
    finally:
        if verifier is not None:
            verifier.close()

    if args.out:
        write_jsonl(args.out, [{"answer_file": p, **r.to_dict()} for p, r in reports])
        print("wrote %d verdict(s) to %s" % (len(reports), args.out), file=sys.stderr)

    counts: dict[str, int] = {}
    for _, report in reports:
        counts[report.status.value] = counts.get(report.status.value, 0) + 1
    print("summary: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))

    if args.strict and any(
        not r.status.answers_the_problem or r.proves_the_theorem is False for _, r in reports
    ):
        return EXIT_UNSOUND
    return EXIT_OK


def cmd_grade(args: argparse.Namespace) -> int:
    """Grade a benchmark: N problems x many participants, three stages."""
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
                "backend %r is unavailable: %s\nStage 2 would be skipped, so `solved` "
                "would mean only that the text screen passed. Re-run without -b to "
                "accept that explicitly, or fix the backend." % (info.name, info.detail),
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
            # structural checks still say something useful alone, and the
            # probes report NOT_RUN rather than passing silently.
            print(
                "note: backend %r is unavailable, so the prover-decidable checks "
                "(elaborates, non-trivial, non-vacuous) will report as not run"
                % args.backend,
                file=sys.stderr,
            )

    judge = _open_judge(args)
    language = problem_set.manifest.language or "lean4"
    checker = StatementChecker(verifier, judge, timeout_s=args.timeout)

    verdicts = []
    without_prose = []
    try:
        for index, problem_id in enumerate(problem_set.ids(), start=1):
            prose = problem_set.prose_for(problem_id)
            if not prose:
                without_prose.append(problem_id)
            verdict = checker.check(
                StatementTask(
                    task_id=problem_id,
                    informal_statement=prose,
                    formal_statement=problem_set.source_for(problem_id),
                    language=language,
                )
            )
            verdicts.append(verdict)
            if not args.quiet:
                failures = verdict.failures
                detail = "  <- %s" % failures[0].detail[:70] if failures else ""
                print(
                    "[%d/%d] %-13s %-16s%s"
                    % (index, len(problem_set), verdict.status.value, problem_id[:16], detail),
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

    if args.strict and any(
        v.status in (StatementStatus.MALFORMED, StatementStatus.SUSPICIOUS) for v in verdicts
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
                "prover, run `match` on a known-good and a known-bad answer instead.",
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
    p_match = sub.add_parser("match", help="does answer.lean prove theorem.lean?")
    p_match.add_argument("theorem", help="the problem file, e.g. theorem.lean")
    p_match.add_argument("answer", nargs="+", help="the submitted file(s)")
    add_backend(
        p_match,
        help="prover for the confirmation probe. Omitted means the text screen "
        "alone, which can refuse an answer but cannot confirm one",
    )
    p_match.add_argument(
        "--target",
        help="theorem name under test. Not needed for the one-theorem-per-file "
        "convention; supply it only when a file holds several statements",
    )
    p_match.add_argument(
        "--allow-unproved",
        action="store_true",
        help="only check that the statement matches, not that it is proved",
    )
    p_match.add_argument(
        "--show-probe",
        action="store_true",
        help="print the source a prover would be given: the problem's proposition "
        "closed with the answer's proof term",
    )
    p_match.add_argument("--out", help="write verdicts here as JSONL")
    p_match.add_argument("--timeout", type=float, default=300.0)
    p_match.add_argument("--quiet", action="store_true")
    p_match.add_argument(
        "--strict", action="store_true", help="exit non-zero unless every answer proves it"
    )
    p_match.set_defaults(func=cmd_match)

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
        help="prover for stage 2. Omitted means stage 2 is skipped and `solved` "
        "reflects the text screen only",
    )
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

    # -- autoformalization --------------------------------------------
    p_audit = sub.add_parser(
        "audit",
        help="check the problem set itself: malformed, trivial or vacuous statements",
    )
    p_audit.add_argument("--problems", required=True, help="directory of problem .lean files")
    add_backend(p_audit, help="prover for the probes (elaborates, non-trivial, non-vacuous)")
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


def main(argv: Sequence[str] | None = None) -> int:
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
