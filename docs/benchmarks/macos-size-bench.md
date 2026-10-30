# macOS size benchmark

Run #3 of the Benchmark workflow, commit `46d7ddcae4cb94f4647a2a92192a7890a98ec4f5` (`46d7ddc`).

https://github.com/ericpeck/galley/actions/runs/39054984807

Numbers are from the `size-bench-macos-14` artifact (`benchmarks/results/size_bench.json`). One MB is 1048576 bytes. Each figure is the median of 3 runs. Cutoffs are log-log interpolation, and each one sits inside a measured pair of sizes.

## Environment

- Runner: `macos-14`, Apple Silicon (Darwin arm64)
- Python 3.12.10
- ruamel.yaml 0.19.1
- Shipped `format_yaml` is `YAML(typ="safe")` with no C extension
- The C-library row is benchmark-local `YAML(typ="safe")` with `ruamel.yaml.clib==0.2.15`
- Round-trip is benchmark-local `YAML(typ="rt")`, pure Python, and is a reference only

## Cutoffs

Sizes where formatting takes about 0.5 s and about 2 s. These are measurements. They are not UI thresholds.

| Row | ~0.5 s | ~2 s |
| --- | --- | --- |
| JSON (`format_json`) | 6.5802 MB | 25.452 MB |
| YAML as shipped (pure Python) | 0.0757 MB | 0.3552 MB |
| YAML with ruamel.yaml.clib | 0.3513 MB | 1.1355 MB |
| YAML round-trip (reference only) | 0.0719 MB | 0.2565 MB |

JSON was measured from 1 MB to 100 MB. Each YAML row was measured from 0.05 MB to 10 MB.

## Speed

The JSON fast path (#24) made JSON 3 to 3.6x faster (1 MB 0.23 s to 0.065 s; 100 MB 32.5 s to 8.9 s). This run measured 0.065089 s at 1 MB and 8.888368 s at 100 MB.

The C library speeds YAML 5.3x at 0.1 MB, 3.3x at 1 MB, and 4.0x at 10 MB. At 1 MB, pure-Python YAML took 5.824235 s and the C library took 1.746122 s.

The 10 MB pure-Python YAML timing is noisy. The three runs were 62.324402 s, 78.947003 s, and 85.314507 s.

## How to rerun

The Benchmark workflow (`.github/workflows/benchmark.yml`) runs only when someone dispatches it. In Actions, choose Benchmark and run it on the commit you want. This run used the script default, `uv run python benchmarks/size_bench.py` (`--corpus plain`). It ran on `macos-14`. The job now uses `macos-15` and Python 3.12, runs `uv sync`, and uploads `benchmarks/results/` as the `size-bench-macos-15` artifact. Later dispatches on the realistic-corpus branch pass `--corpus all`. Trigger rates for that corpus are chosen in the `benchmarks/size_bench.py` module docstring. They are not measured. The bare-tag sample is `channel: ! stable` and the quoted-tab sample is the single-line `"name\tregion"`. Those forms keep the C value. The directive sample is a leading `%YAML 1.2` and `---`, which still re-parses. The generator text is unchanged.

Run #3's shipped YAML row is pure Python. That commit's worker is `uv run --isolated`, and the lockfile had no C extension. Only the safe+C row was given the C library, with `uv run --with ruamel.yaml.clib==0.2.15`. On later commits the same `--isolated` worker installs the lockfile, which includes `ruamel.yaml.clib`, so shipped `format_yaml` uses the C parser when the file does not need a re-parse.
