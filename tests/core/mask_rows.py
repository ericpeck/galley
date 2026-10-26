"""Fake customer documents for the masked Format perf tests (#66, #107).

Every personal value is reserved or made up: ``@example.com`` addresses,
555-01xx phone numbers, ``Example Street`` and the ``00000`` zip code. Each
row masks six values: name, email, phone and the three address values.
Nothing outside ``tests`` imports this module.
"""

import json
import random
from collections.abc import Callable

from galley.core.format import format_document

ONE_MB = 1_000_000
# The long-line guard's fixture size (``test_long_line_guard._BUDGET_CHARS``).
ONE_LINE_CHARS = 1_600_000
MASKED_PER_ROW = 6
SEED = 66

Rows = list[dict[str, object]]


def fake_rows(count: int, seed: int = SEED) -> Rows:
    rng = random.Random(seed)
    words = ("alpha", "beta", "gamma", "delta", "río", "日本", "naïve", "zeta")
    return [
        {
            "id": index,
            "name": f"User {index}",
            "email": f"user{index}@example.com",
            "phone": f"555-01{rng.randrange(100):02d}",
            "address": {
                "street": f"{rng.randrange(1, 999)} Example Street",
                "city": "Springfield",
                "zip": "00000",
            },
            "plan": rng.choice(("free", "team", "business")),
            "balance": round(rng.random() * 1000, 2),
            "active": rng.random() < 0.5,
            "tags": rng.sample(words, 3),
            "note": " ".join(rng.choices(words, k=8)),
        }
        for index in range(count)
    ]


def text_of_size(target_bytes: int, render: Callable[[Rows], str]) -> tuple[str, int]:
    """Grow the row count until ``render(rows)`` is at least ``target_bytes``.

    Returns the text and its row count.
    """
    count = max(1, target_bytes // len(render(fake_rows(1)).encode("utf-8")))
    while True:
        text = render(fake_rows(count))
        size = len(text.encode("utf-8"))
        if size >= target_bytes:
            return text, count
        count += max(1, count * (target_bytes - size) // size) + 1


def pretty_json(rows: Rows) -> str:
    return format_document(json.dumps(rows), "json", "json")


def yaml_text(rows: Rows) -> str:
    return format_document(json.dumps(rows), "json", "yaml")


def one_line(rows: Rows) -> str:
    """Minified JSON: the whole document on one line."""
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
