"""Plain-text find and Keys only (#52, #62), without Qt."""

import inspect
import json
import sys

import pytest
from tests.core.order_lines import QUERY, REPLACE_BYTES, order_lines
from tests.perf import time_runs

from galley.core.find import (
    _yaml_keys,
    astral_offsets,
    find_starts,
    key_mask,
    key_spans,
    replace_all,
    utf16_offsets,
)
from galley.core.format import format_yaml


def _keys(text: str, syntax: str) -> list[str]:
    return [text[start:end] for start, end in key_spans(text, syntax)]


def _keys_only(text: str, syntax: str, query: str, *, match_case=False) -> list[int]:
    return find_starts(key_mask(text, syntax), query, match_case=match_case)


def test_find_is_literal_left_to_right_and_does_not_overlap() -> None:
    assert find_starts("a.b a.b axb", "a.b", match_case=True) == [0, 4]
    assert find_starts("aaaa", "aa", match_case=True) == [0, 2]
    assert find_starts("[x](y)", "](", match_case=True) == [2]


def test_match_case_off_folds_case_and_keeps_the_length() -> None:
    text = "Price PRICE price straße STRASSE"
    assert find_starts(text, "price", match_case=False) == [0, 6, 12]
    assert find_starts(text, "price", match_case=True) == [12]
    # Folding is one character at a time, so ß does not match "ss".
    assert find_starts(text, "strasse", match_case=False) == [25]


@pytest.mark.parametrize("query", ["", "a\nb"])
def test_empty_or_multiline_query_finds_nothing(query: str) -> None:
    assert find_starts("a\nb", query, match_case=False) == []


def test_json_keys_and_values_with_the_same_text() -> None:
    text = json.dumps(
        {"price": "price", "items": [{"price": 1, "note": "price"}], "x": ["price"]},
        indent=2,
    )
    starts = _keys_only(text, "JSON", "price")
    assert len(find_starts(text, "price", match_case=False)) == 5
    assert len(starts) == 2
    for start in starts:
        assert text[start + len("price")] == '"'
        assert text[start + len("price") + 1] == ":"


def test_json_nested_and_escaped_keys_on_one_line() -> None:
    text = '{"a\\"b":{"c":{"price":"\\"price\\": no"}},"d":"x"}'
    assert _keys(text, "JSON") == ['a\\"b', "c", "price", "d"]
    assert len(_keys_only(text, "JSON", "price")) == 1


def test_a_match_never_crosses_from_one_key_into_the_next() -> None:
    text = '{"ab": 1, "cd": 2}'
    assert _keys_only(text, "JSON", "b") == [3]
    assert _keys_only(text, "JSON", "bc") == []
    assert len(key_mask(text, "JSON")) == len(text)


def test_keys_only_respects_match_case() -> None:
    text = '{"Price": "price", "price": 1}'
    assert len(_keys_only(text, "JSON", "price")) == 2
    assert len(_keys_only(text, "JSON", "price", match_case=True)) == 1


YAML_TEXT = """\
name: price
"quoted price": 1
'single price': 2
nested:
  price: 3
  list:
  - price: 4
    other: price
  - &a anchored price: 5
  - !!str tagged price: 6
flow: {price: 7, b: price}
text: |
  price: not a key
# price: comment
😀 price: 8
"""


def test_yaml_keys_from_the_scanner() -> None:
    assert _keys(YAML_TEXT, "YAML") == [
        "name",
        "quoted price",
        "single price",
        "nested",
        "price",
        "list",
        "price",
        "other",
        "anchored price",
        "tagged price",
        "flow",
        "price",
        "b",
        "text",
        "😀 price",
    ]


def test_yaml_keys_only_skips_values_block_scalars_and_comments() -> None:
    assert len(find_starts(YAML_TEXT, "price", match_case=False)) == 13
    starts = _keys_only(YAML_TEXT, "YAML", "price")
    lines = [YAML_TEXT.count("\n", 0, start) + 1 for start in starts]
    assert lines == [2, 3, 5, 7, 9, 10, 11, 15]


def test_yaml_from_the_formatter() -> None:
    text = format_yaml('{"order": {"price": 1, "note": "price"}, "price": "price"}')
    assert len(_keys_only(text, "YAML", "price")) == 2


@pytest.mark.parametrize("pure", [False, True], ids=["default", "pure"])
def test_yaml_bom_moves_the_keys_one_place(pure: bool) -> None:
    text = "\ufeffprice: price\n"
    spans = _yaml_keys(text, pure=pure)
    assert spans == [(1, 6)]
    assert [text[start:end] for start, end in spans] == ["price"]


def test_the_pure_scanner_finds_the_same_keys() -> None:
    for text in (YAML_TEXT, "\ufeff" + YAML_TEXT):
        assert _yaml_keys(text, pure=True) == key_spans(text, "YAML")


def test_text_that_stops_parsing_keeps_the_keys_before_it() -> None:
    assert _keys("a: 1\nb: [\n", "YAML") == ["a", "b"]
    assert _keys('{"a": 1, "b": ', "JSON") == ["a", "b"]


def test_utf16_offsets_count_astral_characters_twice() -> None:
    text = "😀a😀b"
    assert astral_offsets("plain") == []
    astral = astral_offsets(text)
    assert astral == [0, 2]
    starts = find_starts(text, "b", match_case=True)
    assert starts == [3]
    assert utf16_offsets(starts, astral) == [5]
    assert utf16_offsets([0, 1, 2, 4], astral) == [0, 2, 3, 6]


# Replace (release check) ----------------------------------------------------


def test_replace_all_takes_matches_left_to_right_without_overlaps() -> None:
    replaced = replace_all("aaaa", "aa", "b", match_case=True)
    assert replaced.text == "bb"
    assert replaced.spans == [(0, 1), (1, 2)]
    assert replace_all("aaa", "aa", "xy", match_case=True).text == "xya"


def test_replace_all_ignores_case_but_inserts_the_replacement_as_typed() -> None:
    replaced = replace_all("Qty qty QTY", "qty", "Quantity", match_case=False)
    assert replaced.text == "Quantity Quantity Quantity"
    assert replaced.spans == [(0, 8), (9, 17), (18, 26)]
    exact = replace_all("Qty qty QTY", "qty", "Quantity", match_case=True)
    assert exact.text == "Qty Quantity QTY"
    assert exact.spans == [(4, 12)]


def test_replace_all_spans_are_utf16_with_an_astral_character_before_a_match() -> None:
    replaced = replace_all("😀 ab ab", "ab", "x😀y", match_case=True)
    assert replaced.text == "😀 x😀y x😀y"
    # 😀 is two UTF-16 units, in front of the matches and inside each one.
    assert replaced.spans == [(3, 7), (8, 12)]


def test_replace_all_spans_are_utf16_with_an_astral_character_inside_a_match() -> None:
    replaced = replace_all("a😀 b a😀", "a😀", "c", match_case=True)
    assert replaced.text == "c b c"
    assert replaced.spans == [(0, 1), (4, 5)]
    longer = replace_all("a😀 b a😀", "a😀", "😀😀", match_case=True)
    assert longer.text == "😀😀 b 😀😀"
    assert longer.spans == [(0, 4), (7, 11)]


def test_an_empty_replacement_deletes_each_match() -> None:
    replaced = replace_all("a-b-c", "-", "", match_case=True)
    assert replaced.text == "abc"
    assert replaced.spans == [(1, 1), (2, 2)]


@pytest.mark.parametrize("query", ["", "a\nb", "zzz"])
def test_replace_all_with_nothing_to_replace_keeps_the_text(query: str) -> None:
    replaced = replace_all("a\nb", query, "x", match_case=False)
    assert replaced.text == "a\nb"
    assert replaced.spans == []


def test_replace_all_is_plain_text_with_nothing_regex_shaped() -> None:
    assert replace_all("a.b axb", "a.b", "[$1]", match_case=True).text == "[$1] axb"
    parameters = list(inspect.signature(replace_all).parameters)
    assert parameters == ["text", "query", "replacement", "match_case"]


@pytest.mark.perf
def test_replace_all_on_2_4_mb_is_timed() -> None:
    """Timing only, no budget (Isaac): the core function on its own."""
    text = order_lines()
    size = len(text.encode("utf-8"))
    assert REPLACE_BYTES <= size < 2_500_000
    replaced = replace_all(text, QUERY, "quantity", match_case=True)
    assert len(replaced.spans) > 90_000
    timing = time_runs(lambda: replace_all(text, QUERY, "quantity", match_case=True))
    samples = ", ".join(f"{sample * 1000:.1f}" for sample in timing.samples)
    print(
        f"perf: test_replace_all_on_2_4_mb_is_timed [core replace_all, {size:,} B, "
        f"{len(replaced.spans):,} matches] median {timing.median * 1000:.1f} ms "
        f"({sys.platform}; timing only, no budget) ({samples})",
        flush=True,
    )
