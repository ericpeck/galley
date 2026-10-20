"""Reading a file for Open: decoding, size cap, and error kinds."""

from pathlib import Path

import pytest

from galley.core.open_text import (
    MAX_OPEN_BYTES,
    OpenErrorKind,
    OpenTextError,
    format_for_suffix,
    read_text,
    sniff_format,
)


def test_reads_utf8_and_normalizes_newlines(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_bytes("a: é\r\nb: 2\rc: 3\n".encode())
    opened = read_text(path)
    assert opened.text == "a: é\nb: 2\nc: 3\n"
    assert opened.size == len("a: é\r\nb: 2\rc: 3\n".encode())


def test_drops_a_utf8_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(b"\xef\xbb\xbf{}")
    assert read_text(path).text == "{}"


def test_the_cap_is_100_mb_and_a_file_at_the_cap_opens(tmp_path: Path) -> None:
    assert MAX_OPEN_BYTES == 100 * 1_000_000
    path = tmp_path / "a.json"
    path.write_bytes(b"[1, 2]")
    assert read_text(path, limit=6).text == "[1, 2]"
    with pytest.raises(OpenTextError) as caught:
        read_text(path, limit=5)
    assert caught.value.kind is OpenErrorKind.TOO_LARGE


def test_a_file_over_the_cap_is_refused_without_reading(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "huge.json"
    with path.open("wb") as handle:
        handle.truncate(MAX_OPEN_BYTES + 1)

    def _never(_self: Path) -> bytes:
        raise AssertionError("read past the cap")

    monkeypatch.setattr(Path, "read_bytes", _never)
    with pytest.raises(OpenTextError) as caught:
        read_text(path)
    assert caught.value.kind is OpenErrorKind.TOO_LARGE


def test_not_utf8_names_the_line(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(b'{\n\n  "a": "caf\xe9"\n}')
    with pytest.raises(OpenTextError) as caught:
        read_text(path)
    assert caught.value.kind is OpenErrorKind.NOT_UTF8
    assert caught.value.line == 3


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_is_named(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "a.json"
    path.write_bytes('{"a": 1}\n'.encode(encoding))
    with pytest.raises(OpenTextError) as caught:
        read_text(path)
    assert caught.value.kind is OpenErrorKind.UTF16


def test_missing_file_and_folder(tmp_path: Path) -> None:
    with pytest.raises(OpenTextError) as missing:
        read_text(tmp_path / "gone.json")
    assert missing.value.kind is OpenErrorKind.NOT_FOUND
    with pytest.raises(OpenTextError) as folder:
        read_text(tmp_path)
    assert folder.value.kind is OpenErrorKind.IS_DIRECTORY


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (PermissionError(13, "denied"), OpenErrorKind.PERMISSION),
        (OSError(5, "I/O error"), OpenErrorKind.OS_ERROR),
    ],
)
def test_read_errors_are_named(
    tmp_path: Path, monkeypatch, error: OSError, kind: OpenErrorKind
) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(b"{}")

    def _fail(_self: Path) -> bytes:
        raise error

    monkeypatch.setattr(Path, "read_bytes", _fail)
    with pytest.raises(OpenTextError) as caught:
        read_text(path)
    assert caught.value.kind is kind


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [(".json", "JSON"), (".YAML", "YAML"), (".yml", "YAML"), (".txt", None)],
)
def test_format_for_suffix(suffix: str, expected: str | None) -> None:
    assert format_for_suffix(suffix) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('  {"a": 1}', "JSON"),
        ("\n[1]", "JSON"),
        ("# note\nkey: value", "YAML"),
        ("---\na: 1", "YAML"),
        ("- one\n- two", "YAML"),
        ("parent:\n  child: 1", "YAML"),
        ("just some words", None),
        ("http://example.com", None),
        ("", None),
    ],
)
def test_sniff_format(text: str, expected: str | None) -> None:
    assert sniff_format(text) == expected
