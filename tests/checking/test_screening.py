"""Static refusal rules for frozen-target answers."""
from __future__ import annotations

import pytest
from ftp_eval.proving.checking import InterfaceFault, check_interface, read_interface_problem
from ftp_eval.backends.soundness import DEFAULT_ALLOWED_IMPORTS


PROBLEM = "namespace Problem\ndef Target (n : Nat) : Prop := n = 1\nend Problem\n"


ANSWER = "import FtpEvalBench.P001\nnamespace Submission\nvariable (k : Nat)\ntheorem solution : Problem.Target 1 := rfl\nend Submission\n"


def test_submission_variables_are_helpers_not_statement_tampering():
    assert check_interface(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), ANSWER).ok


@pytest.mark.parametrize("signature", ["(n : Nat)", "{n : Nat}", "[Decidable True]"])
def test_solution_binders_refused_before_compilation(signature):
    answer = ANSWER.replace("solution :", "solution %s :" % signature)
    report = check_interface(read_interface_problem(PROBLEM), answer)
    assert InterfaceFault.SOLUTION_BINDERS in [fault for fault, _ in report.faults]


@pytest.mark.parametrize("target", ["theorem Target : True := trivial", "def Target : Nat := 1", "def Target := True"])
def test_target_must_define_a_proposition(target):
    with pytest.raises(ValueError, match="Prop"):
        read_interface_problem("namespace Problem\n%s\nend Problem\n" % target)


def test_import_refusal_names_the_correct_module():
    report = check_interface(read_interface_problem(PROBLEM, module="New.P001"), ANSWER, allowed_imports=("Mathlib",))
    assert "Import New.P001" in report.faults[0][1]


def test_nested_problem_namespace_is_refused():
    answer = ANSWER.replace("variable (k : Nat)", "namespace Problem\ndef fake : Nat := 1\nend Problem")
    report = check_interface(read_interface_problem(PROBLEM), answer)
    assert InterfaceFault.PROBLEM_NAMESPACE_REUSED in [fault for fault, _ in report.faults]


@pytest.mark.parametrize("keyword", ["notation", "infix", "infixl", "infixr", "prefix", "postfix", "macro", "macro_rules", "syntax", "declare_syntax_cat", "elab", "elab_rules", "run_tac"])
def test_answers_cannot_extend_the_checker_language(keyword):
    report = check_interface(read_interface_problem(PROBLEM), ANSWER.replace("variable (k : Nat)", keyword + " fake"))
    assert InterfaceFault.SYNTAX_EXTENSION in [fault for fault, _ in report.faults]


def test_answer_cannot_change_imported_attributes():
    report = check_interface(read_interface_problem(PROBLEM), ANSWER.replace("variable (k : Nat)", "attribute [simp] Problem.Target"))
    assert InterfaceFault.IMPORTED_ATTRIBUTE in [fault for fault, _ in report.faults]


def test_answer_can_mark_its_own_helper_simp():
    answer = ANSWER.replace("variable (k : Nat)", "def helper : Nat := 1\nattribute [simp] helper")
    assert check_interface(read_interface_problem(PROBLEM), answer).ok


def test_default_submission_imports_exclude_lean_metaprogramming():
    report = check_interface(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"),
                             ANSWER.replace("import FtpEvalBench.P001", "import FtpEvalBench.P001\nimport Lean"),
                             allowed_imports=DEFAULT_ALLOWED_IMPORTS)
    assert InterfaceFault.IMPORT_NOT_ALLOWED in [fault for fault, _ in report.faults]


# -- commands that run while the answer elaborates ---------------------


@pytest.mark.parametrize("command", [
    "#eval IO.FS.writeFile \"x\" \"y\"",
    "open Nat in #eval IO.println \"x\"",
    "builtin_initialize IO.println \"x\"",
    "initialize IO.println \"x\"",
])
def test_compile_time_execution_is_refused(command):
    """`#eval` and friends reach the filesystem the grader stages into.

    Verified against Lean 4.29: a `#eval` in an answer can rewrite the
    staged `Problem.olean` between its own compile and the generated
    check, which makes the frozen target say whatever the answer wants.
    The backend's artifact digests catch that too; this refuses it first.
    """
    answer = ANSWER.replace("namespace Submission\n", "namespace Submission\n%s\n" % command)
    report = check_interface(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"), answer)
    assert InterfaceFault.REWARD_HACKING in [fault for fault, _ in report.faults]


@pytest.mark.parametrize("header", [
    "/- note -/ import Lean\n",
    "/- note\n-/ import Lean\n",
    "   import Lean\n",
])
def test_an_import_hidden_behind_a_comment_is_still_refused(header):
    """Lean reads these as `import Lean`; so must the allowlist.

    `Lean` is off the default list because of the metaprogramming and
    unsafe declarations it carries, and an import the reader cannot see
    is an import the allowlist cannot refuse.
    """
    answer = ANSWER.replace("import FtpEvalBench.P001\n", "import FtpEvalBench.P001\n" + header)
    report = check_interface(read_interface_problem(PROBLEM, module="FtpEvalBench.P001"),
                             answer, allowed_imports=DEFAULT_ALLOWED_IMPORTS)
    assert InterfaceFault.IMPORT_NOT_ALLOWED in [fault for fault, _ in report.faults]
