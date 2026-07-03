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

## Option D — Run mode: stdin/stdout scripts (no importable tests)

Best for scripts that read **stdin** and write **stdout** at module top level —
competitive-programming solutions, CLI tools, filters. The default harness
*imports* the target, which crashes such scripts (they call `input()` on
import). Run mode instead executes the target as a **subprocess**, feeds it
stdin, and compares stdout against expected output.

Provide a JSON file of I/O cases:

```json
[
  {"name": "sample",  "input": "2\nab\nbabba\n", "output": "NO\nYES"},
  {"name": "big_no",  "input": "1\nabcabc...\n", "output": "NO"}
]
```

Then point LoopBench at the script with `--io-tests`:

```bash
loopbench run \
  --target . \
  --target-file path/to/solution.py \
  --io-tests path/to/io_tests.json \
  -i 6
```

LoopBench auto-generates a pytest harness that runs `python solution.py` per
case, checks the output, and times the heaviest case for the speed score. You
can also drop the cases in a conventional file next to the target
(`<stem>.io.json` or `io_tests.json`) and omit `--io-tests` — it's auto-detected.

- Correctness = every case's stdout matches (compared line-by-line, trailing
  whitespace ignored).
- The metric = wall-clock time of the largest input.

> Template you can copy: `examples/stdin_palindrome/` (`solution.py` +
> `io_tests.json`). Verified end-to-end: a naive O(n³) stdin solver was evolved
> to O(n), all cases green, on a script that can't even be imported.

---

## Constraints & cost budget

LoopBench is cost-bounded: the loop stops early when a token or dollar budget is
reached (in addition to `--iterations`). This applies to hero mode
(`--target ...`).

```bash
# Stop after 50k total LLM tokens (works with any provider — tokens are
# reported by the API and always enforceable)
loopbench run --target . --target-file src/hot.py --metric latency --max-tokens 50000

# Stop after an estimated $0.25 spend (requires pricing, see below)
loopbench run --target . --target-file src/hot.py --metric latency --max-cost 0.25

# Stop after 300 seconds of wall-clock time
loopbench run --target . --target-file src/hot.py --metric latency --max-runtime 300
```

Or declare them in `loopbench.yaml` under `constraints` (CLI flags override):

```yaml
constraints:
  max_iterations: 20
  max_tokens_total: 50000          # hard token budget
  max_token_cost_usd: 0.25         # dollar budget (needs pricing below)
  max_runtime_seconds: 300         # wall-clock deadline for the whole run
  usd_per_1k_prompt: 0.00059       # your provider's input price per 1k tokens
  usd_per_1k_completion: 0.00079   # output price per 1k tokens
```

The other two constraints from the spec are always on and need no config: the
sandbox runs with `--network=none` (**no external network**) and mounts your
code **read-only** (**no unsafe file writes**).

### Third-party dependencies (numpy, pandas, …)

Real code imports packages that aren't in the base sandbox. LoopBench detects
them and installs them into a cached, per-dependency-set image layered on the
base — the install is the only networked step; the scored run stays
`--network=none`. Detection priority:

1. `--pip "numpy scipy"` (explicit; also settable as `sandbox.pip` in config)
2. a `requirements.txt` at the repo root
3. imports scanned across **every** `.py` file in the repo (import names are
   mapped to PyPI names, and standard-library/local modules are filtered out)

```bash
# Auto-detected from the repo's imports / requirements.txt:
loopbench run --target . --target-file src/model.py --metric latency

# Or pin them explicitly (fast, deterministic):
loopbench run --target . --target-file src/model.py --pip "numpy scipy"
```

The first run with a new dependency set builds the image (a minute or two);
later runs reuse it.

### Custom sandbox commands (any test/benchmark runner)

By default the sandbox runs `pytest`. To use a different command — a benchmark
harness, a type checker, or a plain script — pass `--test-command` (hero mode)
or set `sandbox.command` (config mode):

```bash
loopbench run --target . --target-file src/hot.py \
  --test-command "python benchmark.py"
```

For a **pytest** command, correctness comes from the pass/fail report. For **any
other** command, correctness is the command's **exit code** (`0` = pass). In both
cases, print a `LOOPBENCH_SPEED_MS=<number>` line to feed the speed score.

Token counts come from the provider's `usage` field (OpenAI, Groq, and Google
AI Studio all report it). The dollar estimate needs the two pricing fields — if
they're 0, the USD budget is inactive but the token budget still works. Every
generation's token/cost delta is written to the run's audit log, and the run
summary reports total tokens, estimated cost, and whether the budget stopped it.

### Where to find your pricing

Set `usd_per_1k_prompt` / `usd_per_1k_completion` to your provider's **current**
per-1,000-token rates (pricing changes often — always check the provider page):

- **Groq** — groq.com/pricing
- **OpenAI** — openai.com/api/pricing
- **Google Gemini** — ai.google.dev/pricing

Example: if a provider charges `$0.59` per **million** input tokens, that is
`0.59 / 1000 = 0.00059` per 1k, so `usd_per_1k_prompt: 0.00059`.

### Worked example

A ready-to-use budget block ships in
[`examples/fibonacci_optimizer/loopbench.yaml`](../examples/fibonacci_optimizer/loopbench.yaml).
Run the fibonacci demo with a small token cap:

```bash
loopbench run \
  --target . \
  --target-file examples/fibonacci_optimizer/initial_program.py \
  --metric latency \
  -i 5 \
  --max-tokens 1
```

Because the baseline uses no LLM tokens, generation 1 runs, then the loop stops
at the budget gate before generation 2. The summary reports what was spent:

```
  Total Generations: 1
  ...
  Tokens used    : 896 (1 API calls)
------------------------------------------------------------
```

Swap `--max-tokens 1` for a realistic value (e.g. `--max-tokens 50000`) or use
`--max-cost 0.25` once pricing is set in the config.

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
| Optimize a stdin/stdout script | **D** | `--io-tests` JSON of input/output cases |

All modes are cost-bounded — see [Constraints & cost budget](#constraints--cost-budget)
to cap tokens or dollars.

See the [Quick Start](../QUICKSTART.md) for the end-to-end 5-minute walkthrough.
