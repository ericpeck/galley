"""Cutoff summaries stay inside the sizes that were actually timed."""

import json

from benchmarks.size_bench import _cutoff_section, cutoffs_for


def _rows(samples: list[tuple[float, float]]) -> list[dict[str, object]]:
    return [
        {
            "group": "format",
            "operation_id": "format_json",
            "source_format": "json",
            "cutoff_role": "ui",
            "size_mb": size_mb,
            "median_s": median_s,
        }
        for size_mb, median_s in samples
    ]


def _summary(samples: list[tuple[float, float]]) -> tuple[str, str]:
    rendered = "\n".join(_cutoff_section(cutoffs_for(_rows(samples)), lead_in=True))
    payload = json.dumps(cutoffs_for(_rows(samples)))
    return rendered, payload


def test_cutoff_above_largest_measured_size() -> None:
    samples = [(1.0, 0.02), (10.0, 0.2), (100.0, 0.4)]
    measured = cutoffs_for(_rows(samples))[0]["points"]
    points = {point["target_s"]: point for point in measured}
    for target_s in (0.5, 2.0):
        point = points[target_s]
        assert point["summary"] == "above 100 MB"
        assert point["size_mb"] is None
        assert point["size_bytes"] is None
        assert point["extrapolated"] is False
    rendered, payload = _summary(samples)
    wording = "0.5 s cutoff is above 100 MB; 2 s cutoff is above 100 MB."
    assert wording in rendered
    assert "extrapolation" not in rendered
    assert "above 100 MB" in payload
    assert "log-log extrapolation" not in payload


def test_cutoff_below_smallest_measured_size() -> None:
    samples = [(1.0, 3.0), (5.0, 8.0), (10.0, 20.0)]
    measured = cutoffs_for(_rows(samples))[0]["points"]
    points = {point["target_s"]: point for point in measured}
    for target_s in (0.5, 2.0):
        point = points[target_s]
        assert point["summary"] == "below 1 MB"
        assert point["size_mb"] is None
        assert point["size_bytes"] is None
        assert point["extrapolated"] is False
    rendered, payload = _summary(samples)
    wording = "0.5 s cutoff is below 1 MB; 2 s cutoff is below 1 MB."
    assert wording in rendered
    assert "extrapolation" not in rendered
    assert "below 1 MB" in payload
