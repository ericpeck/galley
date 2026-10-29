"""Regex and Whole word: the engine the find worker runs."""

import re

import pytest

from galley.core.find import astral_offsets, utf16_offsets
from galley.core.regex_find import (
    FindJob,
    Found,
    PatternError,
    PatternFailed,
    ReplaceAllJob,
    ReplacedAll,
    ReplacedOne,
    ReplacementFailed,
    ReplaceOneJob,
    compile_pattern,
    expand,
    find_spans,
    missing_group,
    parse_replacement,
    pattern_flags,
    pattern_source,
    replace_spans,
    replacement_at,
    run_job,
)

_EMOJI = "\U0001f600"


def _spans(
    text: str,
    query: str,
    *,
    regex: bool = True,
    whole_word: bool = False,
    match_case: bool = True,
) -> list[tuple[int, int]]:
    pattern = compile_pattern(
        query, regex=regex, whole_word=whole_word, match_case=match_case
    )
    starts, ends = find_spans(text, pattern)
    return list(zip(starts, ends, strict=True))


def _replace(
    text: str, query: str, replacement: str, *, match_case: bool = True
) -> str:
    pattern = compile_pattern(
        query, regex=True, whole_word=False, match_case=match_case
    )
    return replace_spans(text, pattern, parse_replacement(replacement))[0]


def _job(**changes) -> FindJob:
    fields = {
        "job_id": 1,
        "version": 1,
        "query": "a",
        "regex": True,
        "whole_word": False,
        "match_case": True,
    }
    fields.update(changes)
    return FindJob(**fields)


# Flags ----------------------------------------------------------------------


def test_multiline_is_on_and_dotall_is_off() -> None:
    flags = pattern_flags(match_case=True)
    assert flags & re.MULTILINE
    assert not flags & re.DOTALL
    assert _spans("one\ntwo", "^t") == [(4, 5)]
    assert _spans("a\nb", "a.b") == []


def test_match_case_off_adds_ignorecase() -> None:
    assert pattern_flags(match_case=False) & re.IGNORECASE
    assert not pattern_flags(match_case=True) & re.IGNORECASE
    assert _spans("Qty qty", "qty", match_case=False) == [(0, 3), (4, 7)]
    assert _spans("Qty qty", "qty") == [(4, 7)]


# Whole word -------------------------------------------------------------------


def test_whole_word_wraps_in_lookarounds_not_a_word_boundary() -> None:
    source = pattern_source("a|b", regex=True, whole_word=True)
    assert source == r"(?<!\w)(?:a|b)(?!\w)"
    assert r"\b" not in source


def test_whole_word_escapes_a_literal_first() -> None:
    assert pattern_source("a.b", regex=False, whole_word=True) == (
        r"(?<!\w)(?:a\.b)(?!\w)"
    )
    assert _spans("a.b axb", "a.b", regex=False, whole_word=True) == [(0, 3)]


def test_whole_word_skips_matches_inside_words() -> None:
    text = "id ids _id id_ (id)"
    assert _spans(text, "id", regex=False, whole_word=True) == [(0, 2), (16, 18)]


def test_whole_word_applies_to_every_alternative() -> None:
    text = "cat dog catalog"
    assert _spans(text, "cat|dog", whole_word=True) == [(0, 3), (4, 7)]


def test_whole_word_splits_a_word_at_a_combining_mark() -> None:
    """Known limit: \\w does not match combining marks.

    In a decomposed "café" the mark ends the word, so Whole word finds
    "cafe" inside it. The composed form is one word.
    """
    decomposed = "café"
    composed = "café"
    assert _spans(decomposed, "cafe", regex=False, whole_word=True) == [(0, 4)]
    assert _spans(composed, "caf", regex=False, whole_word=True) == []


# Offsets --------------------------------------------------------------------


def test_spans_count_code_points_and_convert_to_utf16() -> None:
    text = f"{_EMOJI}ab{_EMOJI}cd{_EMOJI}"
    spans = _spans(text, f"b{_EMOJI}c")
    assert spans == [(2, 5)]
    astral = astral_offsets(text)
    starts = utf16_offsets([start for start, _ in spans], astral)
    ends = utf16_offsets([end for _, end in spans], astral)
    # One emoji before the match, one inside it.
    assert (starts, ends) == ([3], [7])
    assert text.encode("utf-16-le")[6:14].decode("utf-16-le") == f"b{_EMOJI}c"


def test_an_empty_match_steps_one_code_point_past_an_emoji() -> None:
    text = f"a{_EMOJI}b"
    spans = _spans(text, "x*")
    assert spans == [(0, 0), (1, 1), (2, 2), (3, 3)]
    astral = astral_offsets(text)
    starts = utf16_offsets([start for start, _ in spans], astral)
    # Never between the emoji's two UTF-16 units.
    assert starts == [0, 1, 3, 4]


def test_caret_matches_each_line_start() -> None:
    assert _spans("a\nb\n", "^") == [(0, 0), (2, 2), (4, 4)]


# Replacement ----------------------------------------------------------------


def test_dollar_digits_name_groups_and_dollar_dollar_is_a_dollar() -> None:
    assert parse_replacement("$1-$0$$x") == (1, "-", 0, "$x")


def test_backslashes_stay_literal() -> None:
    assert parse_replacement(r"\n\1\\") == (r"\n\1\\",)
    assert _replace("ab", "(a)", r"\n\1") == r"\n\1b"


def test_dollar_ten_is_group_one_then_zero() -> None:
    assert parse_replacement("$10") == (1, "0")
    assert _replace("ab", "(a)", "$10") == "a0b"


def test_a_dollar_before_anything_else_is_itself() -> None:
    assert parse_replacement("$a $ $") == ("$a $ $",)
    assert parse_replacement("１$１") == ("１$１",)


def test_an_optional_group_that_did_not_match_is_empty() -> None:
    assert _replace("a ab", "a(b)?", "[$1]") == "[] [b]"


def test_a_missing_group_is_reported() -> None:
    assert missing_group(parse_replacement("$1 $3"), 2) == 3
    assert missing_group(parse_replacement("$1 $2"), 2) is None
    assert missing_group(parse_replacement("$0"), 0) is None


def test_replace_spans_are_in_the_new_text() -> None:
    pattern = compile_pattern("q(t)y", regex=True, whole_word=False, match_case=True)
    text = f"{_EMOJI} qty qty"
    new_text, starts, ends = replace_spans(text, pattern, parse_replacement("<$1>"))
    assert new_text == f"{_EMOJI} <t> <t>"
    assert [new_text[s:e] for s, e in zip(starts, ends, strict=True)] == ["<t>"] * 2


def test_expand_puts_in_whole_match_for_dollar_zero() -> None:
    match = re.match("(a)(b)?", "a")
    assert match is not None
    assert expand((0, "|", 1, "|", 2), match) == "a|a|"


def test_replacement_at_finds_the_match_with_that_span() -> None:
    pattern = compile_pattern(
        r"(?<=x)(\d)", regex=True, whole_word=False, match_case=True
    )
    parts = parse_replacement("[$1]")
    text = "x1 y2 x3"
    assert replacement_at(text, pattern, parts, 7, 8) == "[3]"
    assert replacement_at(text, pattern, parts, 4, 5) is None


# Errors ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "whole_word", "column"),
    [
        ("(a", False, 1),
        ("(a", True, 1),
        ("ab)", True, 3),
        ("[z-a]", False, 2),
        ("a**", False, 3),
    ],
)
def test_a_compile_error_has_a_column_in_the_query(query, whole_word, column) -> None:
    with pytest.raises(PatternError) as caught:
        compile_pattern(query, regex=True, whole_word=whole_word, match_case=True)
    assert caught.value.column == column
    assert caught.value.message[0].isupper()
    assert "position" not in caught.value.message


def test_a_literal_query_never_fails_to_compile() -> None:
    compile_pattern("(a[", regex=False, whole_word=True, match_case=False)


# Jobs -----------------------------------------------------------------------


def test_a_find_job_returns_spans_and_the_group_count() -> None:
    event = run_job(_job(query="(q)(t)y"), "qty qty")
    assert isinstance(event, Found)
    assert list(event.starts) == [0, 4]
    assert list(event.ends) == [3, 7]
    assert event.groups == 2


def test_a_bad_pattern_returns_its_error() -> None:
    event = run_job(_job(query="(a"), "a")
    assert isinstance(event, PatternFailed)
    assert event.column == 1


def test_replace_all_expands_groups_only_when_asked() -> None:
    fields = vars(_job(query="(a)"))
    expanded = run_job(
        ReplaceAllJob(**fields, replacement="$1$1", expand_groups=True), "xa"
    )
    literal = run_job(
        ReplaceAllJob(**fields, replacement="$1$1", expand_groups=False), "xa"
    )
    assert isinstance(expanded, ReplacedAll)
    assert isinstance(literal, ReplacedAll)
    assert expanded.text == "xaa"
    assert literal.text == "x$1$1"


def test_replace_all_with_a_missing_group_changes_nothing() -> None:
    job = ReplaceAllJob(**vars(_job(query="(a)")), replacement="$2", expand_groups=True)
    assert run_job(job, "a") == ReplacementFailed(1, 2)


def test_replace_one_returns_that_matchs_replacement() -> None:
    job = ReplaceOneJob(
        **vars(_job(query=r"(\w+)@")),
        replacement="<$1>",
        expand_groups=True,
        start=4,
        end=8,
    )
    assert run_job(job, "ab@ cde@") == ReplacedOne(1, "<cde>")
