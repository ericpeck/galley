"""Reading a file for Open: decoding, size cap, error kinds, and format."""

import os
import sys
from pathlib import Path

import pytest

from galley.core.load import (
    MAX_OPEN_BYTES,
    LoadError,
    LoadErrorKind,
    detect_format,
    read_document,
)


def test_reads_utf8(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_bytes("a: é\n".encode())
    loaded = read_document(path)
    assert loaded.text == "a: é\n"
    assert loaded.fmt == "YAML"
    assert loaded.size == len("a: é\n".encode())
    assert not loaded.had_bom
    assert loaded.newline == "\n"


def test_drops_a_utf8_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(b"\xef\xbb\xbf{}")
    loaded = read_document(path)
    assert loaded.text == "{}"
    assert "﻿" not in loaded.text
    assert loaded.had_bom
    assert loaded.size == 5


@pytest.mark.parametrize(
    ("data", "newline"),
    [(b"a: 1\r\nb: 2\r\n", "\r\n"), (b"a: 1\rb: 2\r", "\r"), (b"a: 1\nb: 2", "\n")],
)
def test_newlines_become_lf_and_the_style_is_kept(
    tmp_path: Path, data: bytes, newline: str
) -> None:
    path = tmp_path / "a.yaml"
    path.write_bytes(data)
    loaded = read_document(path)
    assert loaded.text in ("a: 1\nb: 2\n", "a: 1\nb: 2")
    assert "\r" not in loaded.text
    assert loaded.newline == newline


def test_an_empty_file_opens(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_bytes(b"")
    loaded = read_document(path)
    assert loaded.text == ""
    assert loaded.size == 0
    assert loaded.fmt == "JSON"
    assert loaded.newline == "\n"


def test_the_cap_is_100_mb_and_a_file_at_the_cap_opens(tmp_path: Path) -> None:
    assert MAX_OPEN_BYTES == 100 * 1_000_000
    path = tmp_path / "a.json"
    path.write_bytes(b"[1, 2]")
    assert read_document(path, max_bytes=6).text == "[1, 2]"
    with pytest.raises(LoadError) as caught:
        read_document(path, max_bytes=5)
    assert caught.value.kind is LoadErrorKind.TOO_LARGE


def test_a_file_over_the_cap_is_refused_by_stat_before_reading(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "huge.json"
    with path.open("wb") as handle:
        handle.truncate(MAX_OPEN_BYTES + 1)

    def _never(_self: Path) -> bytes:
        raise AssertionError("read past the cap")

    monkeypatch.setattr(Path, "read_bytes", _never)
    with pytest.raises(LoadError) as caught:
        read_document(path)
    assert caught.value.kind is LoadErrorKind.TOO_LARGE


def test_invalid_bytes_name_the_offset_and_line(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    data = b'{\n\n  "a": "caf\xe9"\n}'
    path.write_bytes(data)
    with pytest.raises(LoadError) as caught:
        read_document(path)
    assert caught.value.kind is LoadErrorKind.NOT_UTF8
    assert caught.value.offset == data.index(b"\xe9")
    assert caught.value.line == 3


def test_the_offset_counts_a_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    data = b"\xef\xbb\xbf\xff"
    path.write_bytes(data)
    with pytest.raises(LoadError) as caught:
        read_document(path)
    assert caught.value.offset == 3
    assert caught.value.line == 1


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_with_a_byte_order_mark_is_refused(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "a.json"
    text = '{"a": 1}\n'
    data = text.encode(encoding)
    if encoding != "utf-16":
        bom = "﻿".encode(encoding)
        data = bom + data
    path.write_bytes(data)
    with pytest.raises(LoadError) as caught:
        read_document(path)
    assert caught.value.kind is LoadErrorKind.UTF16


def test_missing_file_and_folder(tmp_path: Path) -> None:
    with pytest.raises(LoadError) as missing:
        read_document(tmp_path / "gone.json")
    assert missing.value.kind is LoadErrorKind.NOT_FOUND
    with pytest.raises(LoadError) as folder:
        read_document(tmp_path)
    assert folder.value.kind is LoadErrorKind.IS_DIRECTORY


@pytest.mark.skipif(sys.platform == "win32", reason="chmod can't deny reads")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads anything"
)
def test_unreadable_file(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"
    path.write_bytes(b"{}")
    path.chmod(0)
    try:
        with pytest.raises(LoadError) as caught:
            read_document(path)
    finally:
        path.chmod(0o600)
    assert caught.value.kind is LoadErrorKind.PERMISSION


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (PermissionError(13, "denied"), LoadErrorKind.PERMISSION),
        (OSError(5, "I/O error"), LoadErrorKind.OS_ERROR),
    ],
)
def test_read_errors_are_split_by_kind(
    tmp_path: Path, monkeypatch, error: OSError, kind: LoadErrorKind
) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(b"{}")

    def _fail(_self: Path) -> bytes:
        raise error

    monkeypatch.setattr(Path, "read_bytes", _fail)
    with pytest.raises(LoadError) as caught:
        read_document(path)
    assert caught.value.kind is kind


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("a.json", "a: 1", "JSON"),
        ("a.YAML", "{}", "YAML"),
        ("a.yml", "", "YAML"),
        ("a.txt", '  {"a": 1}', "JSON"),
        ("a.txt", "\n[1]", "JSON"),
        ("a.txt", "# note\nkey: value", "YAML"),
        ("a.txt", "---\na: 1", "YAML"),
        ("a.txt", "- one\n- two", "YAML"),
        ("a.txt", "parent:\n  child: 1", "YAML"),
        ("a.txt", "just some words", None),
        ("a.txt", "http://example.com", None),
        ("a.geojson", "type: x", "JSON"),
        ("A.GEOJSON", "", "JSON"),
        ("a.jsonc", "", None),
        ("a.jsonc", '{"a": 1, // note\n}', "JSON"),
        ("a.jsonc", "key: value", "YAML"),
        ("a.json5", "[1,]", "JSON"),
        ("noext", "", None),
    ],
)
def test_detect_format(name: str, text: str, expected: str | None) -> None:
    assert detect_format(Path(name), text) == expected
