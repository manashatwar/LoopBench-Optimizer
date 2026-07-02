# Defining Your Benchmark

A "benchmark" in LoopBench is two things working together:

1. **A correctness gate** — your tests pass/fail. Any failure forces the score to
   `0.0`, so a candidate that breaks behavior is always rejected.
2. **A metric to optimize** — a number (latency, memory, accuracy, …) that
   LoopBench tries to improve, generation after generation.

These combine into a single `combined_score` that drives evolution:

```
combined_score = correctness × metric_score
```

There are three ways to define this, from fastest to most flexible. Pick one.

---

## Option A — Hero mode (fastest): tests + a speed marker

Best when you just want to point LoopBench at a file and go. The benchmark is
your repo's own pytest suite. You need two things in a `test_*.py` file:

- Assertions that verify correctness.
- One test that measures the hot path and prints a marker line:
  `LOOPBENCH_SPEED_MS=<number>`.

### Full flow

```bash
# 0. Docker must be running
docker info

# 1. Your project layout — the test lives next to the file being optimized
#    my_repo/
#    ├── slow_module.py        <- file to optimize
#    └── test_slow_module.py   <- correctness + speed marker
```

Write the test so it emits the speed marker:

```python
# test_slow_module.py
import importlib.util, os, time, types
import pytest

_PATH = os.environ["LOOPBENCH_PROGRAM_PATH"]  # set by LoopBench

def _load():
    spec = importlib.util.spec_from_file_location("evolved", _PATH)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

@pytest.fixture(scope="session")
def prog(): return _load()

# ── correctness gate ──
def test_correct(prog):
    assert prog.solve(10) == 55

# ── the metric being optimized ──
def test_speed(prog):
    start = time.perf_counter()
    prog.solve(50_000)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")   # <- LoopBench reads this
    assert elapsed_ms < 5000                          # hard safety limit
```

Run the optimization:

```bash
loopbench run \
  --target . \
  --target-file slow_module.py \
  --metric latency \
  -i 5

# Optional: override the auto-detected test command
loopbench run --target . --target-file slow_module.py \
  --test-command "pytest test_slow_module.py -s -q" -i 5
```

### Where the score is computed

The scoring formula lives in **`sandbox/entrypoint.sh`**:

```python
correctness    = 1.0 if all_passed else 0.0
speed_score    = exp(-speed_ms / 150.0)     # tune the 150.0 for your latency scale
combined_score = correctness * speed_score
```

If your latencies are much larger (e.g. thousands of ms), raise the `150.0`
decay constant so scores spread meaningfully across your range.

> Template you can copy: `examples/prime_counter_optimizer/test_prime_counter.py`

---

## Option B — Evaluator-first (most control): your own scoring formula

Best when you want to define exactly how the metric is computed and combined
(e.g. weight accuracy vs. speed vs. memory). The benchmark is an `evaluator.py`
that returns the metrics.

### Full flow

```bash
# 1. Scaffold a project config
loopbench init --name my_project
#   creates my_project.yaml
```

Create the three files (see `examples/fibonacci_optimizer/` for a full set):

```
my_project/
├── initial_program.py     # code to optimize (wrap the mutable part in EVOLVE-BLOCK)
├── test_my_project.py     # pytest suite
├── evaluator.py           # returns the benchmark score
└── loopbench.yaml         # wiring + stop target
```

Your `evaluator.py` defines the benchmark:

```python
from openevolve.evaluation_result import EvaluationResult

def evaluate(program_path: str) -> EvaluationResult:
    correctness = ...   # 1.0 if all tests pass else 0.0
    speed_ms    = ...   # measured from your test output
    speed_score = ...   # your own formula
    return EvaluationResult(metrics={
        "correctness": correctness,
        "speed_ms": speed_ms,
        "speed_score": speed_score,
        "combined_score": correctness * speed_score,   # <- the benchmark
    })
```

Set the stop target in `loopbench.yaml`:

```yaml
target:
  program: initial_program.py
  evaluator: evaluator.py

sandbox:
  use_docker: true
  command: "pytest test_my_project.py -v -s -q --tb=short"

metric:
  name: "combined_score"
  threshold: 0.95        # stop once a candidate reaches this score
  direction: "maximize"

constraints:
  max_iterations: 20
```

Validate, then run:

```bash
# Dry-run the evaluator against the initial program (no LLM calls)
loopbench check --config my_project/loopbench.yaml

# Run the optimization
loopbench run --config my_project/loopbench.yaml

# Override iterations / stop target from the CLI if you want
loopbench run --config my_project/loopbench.yaml -i 30 -t 0.98
```

> Template you can copy: `examples/fibonacci_optimizer/` (all four files).

---

## Option C — Custom regex metric: parse numbers from existing test output

Best when your tests already print performance numbers in their own format and
you don't want to adopt the `LOOPBENCH_SPEED_MS` convention. LoopBench extracts
the metric with a regex via its `MetricParser`.

Add a `metrics` section to your config:

```yaml
metrics:
  patterns:
    execution_time: 'Mean:\s*([\d.]+)\s*seconds'   # captures the number in group 1
    throughput:     'ops/sec:\s*([\d.]+)'
  success_threshold: 0.10        # min improvement to count as a win
```

Then run as in Option B:

```bash
loopbench run --config my_project/loopbench.yaml
```

LoopBench runs your tests, greps each pattern out of stdout/stderr, and uses the
captured values as the metrics for scoring.

---

## Inspecting the benchmark result

Every run writes artifacts to `loopbench_output/` (hero mode) or your
configured output dir:

```bash
# The winning diff
cat loopbench_output/best.patch

# Before/after metrics and patch status
cat loopbench_output/report/validation_report.md

# Proof the winning candidate kept all tests passing
cat loopbench_output/test_log.txt
```

The run also writes `docs/data.json`; view the trajectory on the dashboard:

```bash
python -m http.server 8080 --directory docs   # then open http://localhost:8080
```

---

## Which option should I use?

| You want… | Use | Where you set the benchmark |
|-----------|-----|------------------------------|
| Point-and-go on a file/repo | **A** | `test_*.py` (`LOOPBENCH_SPEED_MS`) + `sandbox/entrypoint.sh` formula |
| Full control over the score formula | **B** | `evaluator.py` + `metric.threshold` in `loopbench.yaml` |
| Reuse existing perf output | **C** | `metrics.patterns` regex in the config |

See the [Quick Start](../QUICKSTART.md) for the end-to-end 5-minute walkthrough.
