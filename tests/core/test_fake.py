"""Tests for Random fake values. All data here is fake."""

import datetime as dt
import random
import re

import pytest

from galley.core.fake import FAKE_EMAIL_DOMAIN, fake_value


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("Pat Example", "other"),
        ("pat.example@example.com", "email"),
        ("+1 (555) 010-0199", "phone"),
        (4021, "other"),
        (-385, "other"),
        (12.75, "other"),
        (True, "other"),
        (dt.date(2001, 2, 3), "other"),
        (dt.datetime(2001, 2, 3, 4, 5, 6), "other"),
        (b"\x00\x01\x02", "other"),
    ],
)
def test_fake_value_keeps_the_type(value: object, kind: str) -> None:
    faked = fake_value(value, kind, random.Random(1))  # type: ignore[arg-type]
    assert type(faked) is type(value)


def test_fake_value_keeps_none() -> None:
    assert fake_value(None, "other") is None


def test_fake_email_uses_example_domain() -> None:
    faked = fake_value("pat.example@mail.test", "email", random.Random(2))
    assert isinstance(faked, str)
    local, _, domain = faked.partition("@")
    assert domain == FAKE_EMAIL_DOMAIN
    assert len(local) == len("pat.example")
    assert re.fullmatch(r"[a-z][a-z0-9]*", local)


def test_fake_email_local_part_has_a_floor() -> None:
    faked = fake_value("a@example.com", "email", random.Random(2))
    assert isinstance(faked, str)
    assert len(faked.partition("@")[0]) == 3


def test_fake_phone_keeps_the_pattern() -> None:
    phone = "+1 (555) 010-0199"
    faked = fake_value(phone, "phone", random.Random(3))
    assert isinstance(faked, str)
    assert re.sub(r"\d", "0", faked) == re.sub(r"\d", "0", phone)


def test_fake_text_keeps_length_case_and_punctuation() -> None:
    text = "Unit 7B, Example Street"
    faked = fake_value(text, "other", random.Random(4))
    assert isinstance(faked, str)
    assert len(faked) == len(text)
    assert re.fullmatch(r"[A-Z][a-z]{3} \d[A-Z], [A-Z][a-z]{6} [A-Z][a-z]{5}", faked)


@pytest.mark.parametrize("number", [7, 4021, -385, 10**20])
def test_fake_int_keeps_sign_and_digit_count(number: int) -> None:
    faked = fake_value(number, "other", random.Random(5))
    assert isinstance(faked, int)
    assert (faked < 0) == (number < 0)
    assert len(str(abs(faked))) == len(str(abs(number)))


@pytest.mark.parametrize("number", [12.75, -3.5, 1.5e-07, float("inf")])
def test_fake_float_keeps_sign_and_length(number: float) -> None:
    faked = fake_value(number, "other", random.Random(6))
    assert isinstance(faked, float)
    assert (faked < 0) == (number < 0)
    assert len(repr(faked)) == len(repr(number))


def test_fake_float_stays_finite_at_the_top_of_the_range() -> None:
    for seed in range(20):
        faked = fake_value(1.7976931348623157e308, "other", random.Random(seed))
        assert isinstance(faked, float)
        assert faked != float("inf")


def test_fake_bool_is_random() -> None:
    results = {fake_value(True, "other", random.Random(seed)) for seed in range(10)}
    assert results == {True, False}


def test_fake_datetime_keeps_tzinfo() -> None:
    value = dt.datetime(2001, 2, 3, 4, 5, 6, tzinfo=dt.UTC)
    faked = fake_value(value, "other", random.Random(7))
    assert isinstance(faked, dt.datetime)
    assert faked.tzinfo is dt.UTC
    assert faked.microsecond == 0


def test_fake_bytes_keep_length() -> None:
    faked = fake_value(b"abcdef", "other", random.Random(8))
    assert isinstance(faked, bytes)
    assert len(faked) == 6


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("pat.example@example.com", "email"),
        ("+1 (555) 010-0199", "phone"),
        ("100 Example Street", "other"),
        (1234567890123, "other"),
    ],
)
def test_two_calls_differ(value: object, kind: str) -> None:
    assert fake_value(value, kind) != fake_value(value, kind)  # type: ignore[arg-type]


def test_seeded_rng_is_deterministic() -> None:
    values = ["pat.example@example.com", "+1 (555) 010-0199", "Pat Example", 4021, 2.5]
    kinds = ["email", "phone", "other", "other", "other"]
    first = [
        fake_value(value, kind, rng)  # type: ignore[arg-type]
        for rng in [random.Random(99)]
        for value, kind in zip(values, kinds, strict=True)
    ]
    second = [
        fake_value(value, kind, rng)  # type: ignore[arg-type]
        for rng in [random.Random(99)]
        for value, kind in zip(values, kinds, strict=True)
    ]
    assert first == second


def test_default_rng_is_system_random(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[object] = []
    real = random.SystemRandom

    class Spy(real):  # type: ignore[valid-type, misc]
        def __init__(self) -> None:
            created.append(self)
            super().__init__()

    monkeypatch.setattr(random, "SystemRandom", Spy)
    fake_value("Pat Example", "other")
    assert len(created) == 1


def test_unknown_types_raise() -> None:
    with pytest.raises(TypeError):
        fake_value(object(), "other")
