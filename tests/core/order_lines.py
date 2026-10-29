"""Fake order lines for the Find and Replace timings.

Each line is one compact JSON object with ``qty`` twice, so 2.4 MB holds
about 100,000 matches of ``qty``. Built in memory; nothing is written to
disk.
"""

import json

REPLACE_BYTES = 2_400_000
QUERY = "qty"


def order_lines(target_bytes: int = REPLACE_BYTES) -> str:
    """A JSON array of at least ``target_bytes`` with one order per line."""
    lines: list[str] = []
    size = 2
    index = 0
    while size < target_bytes:
        line = json.dumps(
            {"sku": f"SKU-{index:06d}", "qty": index % 9 + 1, "qty_max": 9}
        )
        lines.append(line)
        size += len(line) + 4
        index += 1
    return "[\n  " + ",\n  ".join(lines) + "\n]\n"
