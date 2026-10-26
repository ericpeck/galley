"""The Open Recent list: order, de-duplication, the cap, and labels."""

import os
from pathlib import Path

from galley.core.recent import RECENT_LIMIT, add_recent, clean_recent, recent_labels


def test_add_puts_the_newest_first(tmp_path: Path) -> None:
    first = str(tmp_path / "a.json")
    second = str(tmp_path / "b.json")

    entries = add_recent(add_recent([], first), second)

    assert entries == [second, first]


def test_adding_again_moves_the_entry_to_the_top_once(tmp_path: Path) -> None:
    a, b, c = (str(tmp_path / name) for name in ("a.json", "b.yaml", "c.yml"))

    entries = add_recent([c, b, a], a)

    assert entries == [a, c, b]


def test_the_same_file_spelled_differently_is_one_entry(tmp_path: Path) -> None:
    plain = str(tmp_path / "a.json")
    dotted = str(tmp_path / "x" / ".." / "a.json")

    assert add_recent([plain], dotted) == [dotted]


def test_windows_matches_paths_whatever_their_case(tmp_path: Path) -> None:
    lower = str(tmp_path / "a.json")
    upper = str(tmp_path / "A.JSON")

    entries = add_recent([lower], upper)

    if os.name == "nt":
        assert entries == [upper]
    else:
        assert entries == [upper, lower]


def test_the_list_keeps_the_ten_newest(tmp_path: Path) -> None:
    entries: list[str] = []
    for index in range(RECENT_LIMIT + 3):
        entries = add_recent(entries, str(tmp_path / f"{index}.json"))

    assert RECENT_LIMIT == 10
    assert len(entries) == RECENT_LIMIT
    assert entries[0] == str(tmp_path / "12.json")
    assert entries[-1] == str(tmp_path / "3.json")


def test_clean_drops_junk_and_repeats_from_settings(tmp_path: Path) -> None:
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    many = [str(tmp_path / f"{index}.json") for index in range(20)]

    assert clean_recent([a, "", None, 3, a, b]) == [a, b]
    assert clean_recent(many) == many[:RECENT_LIMIT]


def test_labels_show_only_the_file_name(tmp_path: Path) -> None:
    entries = [str(tmp_path / "configs" / "a.json"), str(tmp_path / "b.yaml")]

    assert recent_labels(entries) == ["a.json", "b.yaml"]


def test_labels_add_the_parent_folder_when_names_match(tmp_path: Path) -> None:
    entries = [
        str(tmp_path / "staging" / "data.json"),
        str(tmp_path / "prod" / "Data.json"),
        str(tmp_path / "prod" / "other.json"),
    ]

    assert recent_labels(entries) == [
        "data.json — staging",
        "Data.json — prod",
        "other.json",
    ]
    for label in recent_labels(entries):
        assert str(tmp_path) not in label
