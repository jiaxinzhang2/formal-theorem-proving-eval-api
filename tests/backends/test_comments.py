"""Comment stripping, which every layer depends on being exactly right.

Both directions matter: a comment that *mentions* a hack token must not
fail an honest proof, and a hack token *hidden inside* a comment must not
pass a dishonest one. Everything downstream -- the reward-hacking screen,
tactic extraction, every structural metric -- is computed on the stripped
text, so a bug here is a bug everywhere at once.
"""

from __future__ import annotations

import pytest

from ftp_eval.backends.comments import COMMENT_SYNTAX, strip_comments


# -- both directions ---------------------------------------------------


def test_a_comment_mentioning_sorry_is_removed():
    assert "sorry" not in strip_comments("simp -- no sorry needed", "lean4")


def test_a_sorry_hidden_in_a_block_comment_is_removed():
    assert "sorry" not in strip_comments("simp /- sorry -/", "lean4")


def test_code_outside_comments_survives():
    assert "nlinarith" in strip_comments("nlinarith /- hint -/ [sq_nonneg n]", "lean4")


# -- the two claims the docstring makes --------------------------------


def test_comments_become_whitespace_not_nothing():
    """Otherwise stripping could glue two tokens into a different one.

    `simp/- x -/[foo]` collapsing to `simp[foo]` would change what the
    text means, and every downstream tokenizer would see the wrong thing.
    """
    stripped = strip_comments("simp/- x -/[foo]", "lean4")
    assert "simp[foo]" not in stripped
    assert stripped.split() == ["simp", "[foo]"]


def test_block_comments_are_stripped_before_line_comments():
    """A `--` inside a block comment must not eat the block's terminator.

    If the line-comment rule ran first, `/- -- -/` would lose its closing
    `-/` to the line comment and leave a dangling `/-`, so every later
    check would see a mangled body.
    """
    stripped = strip_comments("theorem t /- -- -/ : True := trivial", "lean4")
    assert "/-" not in stripped
    assert "-/" not in stripped
    assert "theorem t" in stripped
    assert "trivial" in stripped


# -- per language ------------------------------------------------------


@pytest.mark.parametrize(
    "language,source,removed",
    [
        ("lean4", "x -- gone\ny", "gone"),
        ("lean4", "x /- gone -/ y", "gone"),
        ("lean3", "x -- gone", "gone"),
        ("coq", "x (* gone *) y", "gone"),
        ("isabelle", "x (* gone *) y", "gone"),
    ],
)
def test_each_language_syntax(language, source, removed):
    assert removed not in strip_comments(source, language)


def test_an_unknown_language_is_returned_unchanged():
    # Guessing at comment syntax would be worse than not stripping: it
    # could delete real code.
    source = "x (* not necessarily a comment here *) y"
    assert strip_comments(source, "agda") == source


def test_multiline_block_comments_are_handled():
    source = "theorem t\n/- line one\n   line two\n   sorry -/\n:= trivial"
    stripped = strip_comments(source, "lean4")
    assert "sorry" not in stripped
    assert "trivial" in stripped


def test_block_comments_nest_as_lean_does():
    """Lean's block comments nest, and so does the stripper.

    A non-greedy regex stops at the first `-/`, leaving `sorry -/` looking
    like live code. That only ever produced noise -- an honest file flagged
    -- but the scanner gets it right, so the noise is gone.
    """
    assert strip_comments("/- outer /- inner -/ sorry -/", "lean4").strip() == ""


def test_an_unterminated_block_comment_swallows_the_rest():
    # Which is what Lean does too: the file does not compile, and nothing
    # after the opener is code.
    assert strip_comments("theorem t : True := by trivial\n/- oops", "lean4").count(
        "trivial"
    ) == 1
    assert "oops" not in strip_comments("/- oops\ntheorem t : True", "lean4")


def test_a_comment_marker_inside_a_string_literal_is_not_a_comment():
    """The one that was exploitable.

    `s = "a -- ALPHA"` and `s = "a -- OMEGA"` are different statements. A
    regex stripper deletes from `--` to end of line, so both normalize to
    `s = "a ` and two different theorems compare equal -- a false match,
    which is the expensive direction.
    """
    alpha = strip_comments('theorem t : s = "a -- ALPHA" := by rfl', "lean4")
    omega = strip_comments('theorem t : s = "a -- OMEGA" := by rfl', "lean4")
    assert "ALPHA" in alpha
    assert "OMEGA" in omega
    assert alpha != omega


def test_a_block_comment_opener_inside_a_string_is_not_a_comment():
    kept = strip_comments('theorem t : s = "/- not a comment -/" := by rfl', "lean4")
    assert "not a comment" in kept
    assert "rfl" in kept


def test_escaped_quotes_do_not_end_the_string_early():
    kept = strip_comments(r'theorem t : s = "he said \"-- hi\"" := by rfl', "lean4")
    assert "hi" in kept
    assert "rfl" in kept


def test_a_real_comment_after_a_string_is_still_stripped():
    stripped = strip_comments('theorem t : s = "keep" := by rfl -- drop this', "lean4")
    assert "keep" in stripped
    assert "drop this" not in stripped


def test_every_declared_language_has_at_least_one_rule():
    assert COMMENT_SYNTAX
    for language, rules in COMMENT_SYNTAX.items():
        assert rules, "%s declares no comment syntax" % language
        for pattern, replacement in rules:
            assert isinstance(pattern, str)
            assert replacement == " ", "replacements must be whitespace, not empty"


def test_empty_input():
    assert strip_comments("", "lean4") == ""


# -- lexical forms that look like comment delimiters --------------------
#
# Every one of these was verified against Lean 4.29 before it was fixed:
# Lean compiles the file and runs the command, while a scanner that does
# not know the form deletes it from the text the screen reads. Deleting
# live code is the expensive direction -- `sorry`, a fresh `axiom`,
# `native_decide` and `#eval` all go invisible at once.


HIDDEN = "#eval IO.println \"ran\""


@pytest.mark.parametrize("before,after", [
    # `«...»` holds arbitrary characters, so these are two declarations.
    ("def «/-» : Nat := 0", "def «-/» : Nat := 1"),
    # `'"'` is one character, not the start of a string literal.
    ("def c : Char := '\"'\ndef s : String := \"/-\"", "def t : String := \"-/\""),
    # A raw string ends at a quote followed by as many hashes as it opened.
    ("def r : String := r#\"a \"/- b\"#", "def q : String := r#\"c -/ d\"#"),
    # A backslash before the newline continues the literal.
    ("def g : String := \"abc \\n  /- def\"", "def h : String := \"-/\""),
])
def test_a_command_between_lookalike_delimiters_is_not_stripped(before, after):
    source = "namespace Submission\n%s\n%s\n%s\nend Submission\n" % (before, HIDDEN, after)
    assert "#eval" in strip_comments(source, "lean4")


def test_a_trailing_prime_is_an_identifier_not_a_character_literal():
    source = "theorem h' : True := trivial\n%s\n" % HIDDEN
    assert "#eval" in strip_comments(source, "lean4")


def test_an_unterminated_quoted_identifier_does_not_swallow_the_file():
    assert "#eval" in strip_comments("def «open : Nat := 0\n%s\n" % HIDDEN, "lean4")
