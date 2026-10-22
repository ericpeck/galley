"""Random stand-in values for Copy As > Random fake.

This module does not import Qt and uses no fake-data library. Each value
keeps its Python type and about its length. The default source is
``random.SystemRandom``, so every copy gets new values. Tests pass a seeded
``random.Random``.
"""

import datetime as dt
import math
import random
import string
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from galley.core.mask import Kind

FAKE_EMAIL_DOMAIN = "example.com"

_LOCAL_MIN = 3
_LOCAL_MAX = 64
_FAKE_YEARS = (1900, 2099)


def fake_value(value: object, kind: "Kind", rng: random.Random | None = None) -> object:
    """Return a random value of the same type as ``value``.

    A string stays a string of the same length. An email becomes
    ``<random>@example.com`` and a phone keeps its separators with every
    digit random. An int or float keeps its sign and digit count, a bool
    is a random bool, a date or datetime is a random one, bytes stay the
    same length, and ``None`` stays ``None``. Any other type raises
    ``TypeError`` so a caller never copies a value it could not replace.
    """
    chooser = rng if rng is not None else random.SystemRandom()
    if value is None:
        return None
    if isinstance(value, bool):
        return chooser.random() < 0.5
    if isinstance(value, int):
        return random_int_like(value, chooser)
    if isinstance(value, float):
        return random_float_like(value, chooser)
    if isinstance(value, str):
        return _fake_text(value, kind, chooser)
    if isinstance(value, dt.date):
        return random_date_like(value, chooser)
    if isinstance(value, bytes):
        return chooser.randbytes(len(value))
    raise TypeError(f"no fake value for {type(value).__name__}")


def random_digit(rng: random.Random) -> str:
    return str(rng.randrange(10))


def random_int_like(value: int, rng: random.Random) -> int:
    """Return a random int with the sign and digit count of ``value``."""
    width = len(str(abs(value)))
    if width == 1:
        magnitude = rng.randrange(10)
    else:
        magnitude = rng.randrange(10 ** (width - 1), 10**width)
    return -magnitude if value < 0 else magnitude


def random_float_like(value: float, rng: random.Random) -> float:
    """Return a random float written with the same digits as ``value``.

    Each digit of ``repr(value)`` before the exponent is random. The
    leading digit stays non-zero (a lone ``0`` stays ``0``) and the last
    fractional digit stays non-zero, so the digit count holds. The
    exponent is kept. NaN and infinity have no digits and come back as is.
    """
    if not math.isfinite(value):
        return value
    mantissa, marker, exponent = repr(abs(value)).partition("e")
    whole, point, fraction = mantissa.partition(".")
    if whole == "0":
        new_whole = "0"
    else:
        new_whole = str(rng.randrange(1, 10)) + "".join(
            random_digit(rng) for _ in whole[1:]
        )
    if fraction in {"", "0"}:
        new_fraction = fraction
    else:
        new_fraction = "".join(random_digit(rng) for _ in fraction[:-1]) + str(
            rng.randrange(1, 10)
        )
    result = float(new_whole + point + new_fraction + marker + exponent)
    if not math.isfinite(result):
        result = float("1e" + exponent)
    return math.copysign(result, value)


def random_date_like(value: dt.date, rng: random.Random) -> dt.date:
    """Return a random date, or a random datetime with the same tzinfo."""
    year = rng.randint(*_FAKE_YEARS)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    if not isinstance(value, dt.datetime):
        return dt.date(year, month, day)
    microsecond = rng.randrange(1, 1_000_000) if value.microsecond else 0
    return dt.datetime(
        year,
        month,
        day,
        rng.randrange(24),
        rng.randrange(60),
        rng.randrange(60),
        microsecond,
        tzinfo=value.tzinfo,
    )


def _fake_text(value: str, kind: "Kind", rng: random.Random) -> str:
    if kind == "email":
        local = value.rpartition("@")[0] or value
        length = min(max(len(local), _LOCAL_MIN), _LOCAL_MAX)
        name = rng.choice(string.ascii_lowercase) + "".join(
            rng.choice(string.ascii_lowercase + string.digits)
            for _ in range(length - 1)
        )
        return f"{name}@{FAKE_EMAIL_DOMAIN}"
    if kind == "phone" and any(char.isdigit() for char in value):
        return "".join(random_digit(rng) if char.isdigit() else char for char in value)
    return "".join(_fake_char(char, rng) for char in value)


def _fake_char(char: str, rng: random.Random) -> str:
    if char.isdigit():
        return random_digit(rng)
    if char.isupper():
        return rng.choice(string.ascii_uppercase)
    if char.isalpha():
        return rng.choice(string.ascii_lowercase)
    return char
