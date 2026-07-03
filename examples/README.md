# LoopBench Examples

This directory contains runnable examples and subsystem demos for LoopBench
Optimizer. Each optimizer example is a small, self-contained project you can
point the optimizer at to watch it evolve faster code while keeping every test
green.

> **Optimizing an external repo instead of these examples?** You don't add files
> here — scaffold a job folder in your own workspace and point it at the repo:
> ```bash
> loopbench init --job my_job
> loopbench run --config my_job/loopbench.yaml
> ```
> See [Defining Your Benchmark → Optimizing an external repo](../docs/defining-benchmarks.md).
> The examples below use the same building blocks (a test that prints
> `LOOPBENCH_SPEED_MS`), just bundled as self-contained demos.

## Contents

| Path | What it is |
|------|------------|
| [`fibonacci_optimizer/`](fibonacci_optimizer/) | Hello-world optimizer: naive recursive Fibonacci → memoized/iterative |
| [`prime_counter_optimizer/`](prime_counter_optimizer/) | Naive trial-division prime counting → Sieve of Eratosthenes |
| [`json_parser_optimizer/`](json_parser_optimizer/) | Hand-written JSON parser with a concatenation bottleneck (correctness verified against `json.loads`) |
| [`palindrome_optimizer/`](palindrome_optimizer/) | Longest palindromic substring (CodeChef PRINCESS): naive O(n³) → expand-around-center O(n²) |
| [`stdin_palindrome/`](stdin_palindrome/) | **Run mode** demo: a stdin/stdout script (reads `input()`) optimized via subprocess I/O test cases, no import needed |
| [`numpy_vectorize_optimizer/`](numpy_vectorize_optimizer/) | Third-party deps demo: a NumPy Python-loop MSE the sandbox **auto-installs NumPy** to run, then vectorizes |
| [`gradient_descent_optimizer/`](gradient_descent_optimizer/) | Linear-regression gradient descent (naive Python loops → NumPy vectorization); auto-installs NumPy |
| [`llm_prompt_optimization/`](llm_prompt_optimization/) | Prompt-evolution example (also used by the template-resolution tests) |
| [`algotune/`](algotune/) | Real AlgoTune task projects used as fixtures for the repo-context mapper tests |
| `repo_mapper_demo.py` | Demo of `RepoContextMapper` — builds an LLM-ready context map of a repo |
| `repo_mapper_config_examples.yaml` | Example `RepoMapperConfig` presets |
| `metric_parser_demo.py` | Demo of the regex/JSON `MetricParser` |
| `metric_parser_evaluator_demo.py` | Demo wiring the metric parser into an evaluator |
| `candidate_db_demo.py` | Demo of the SQLite `CandidateDatabase` audit trail |

## Running an optimizer example

Set your LLM key (any OpenAI-compatible provider works — Groq, Gemini, OpenAI):

```bash
# .env at the repo root
GEMINI_API_KEY="your-api-key"
LLM_API_BASE="https://api.groq.com/openai/v1"
LLM_MODEL="llama-3.3-70b-versatile"
```

**Hero command** — point the optimizer at the file to improve:

```bash
loopbench run \
  --target . \
  --target-file examples/prime_counter_optimizer/initial_program.py \
  --metric latency \
  -i 5
```

**Config-driven** — each example ships a `loopbench.yaml`:

```bash
loopbench check --config examples/prime_counter_optimizer/loopbench.yaml
loopbench run   --config examples/prime_counter_optimizer/loopbench.yaml
```

## Anatomy of an example

Each optimizer example has four parts:

### 1. `initial_program.py`
The starting (slow but correct) implementation. The region the optimizer is
allowed to rewrite is wrapped in an `EVOLVE-BLOCK`:

```python
# EVOLVE-BLOCK-START
def count_primes(n: int) -> int:
    ...  # only this region is mutated
# EVOLVE-BLOCK-END

def run_count_primes(n: int) -> int:
    # fixed public entry point — never mutated
    return count_primes(n)
```

### 2. `test_*.py`
A pytest suite that acts as the correctness gate. It loads the evolved program
from the `LOOPBENCH_PROGRAM_PATH` environment variable and prints a parseable
speed marker on stdout:

```
LOOPBENCH_SPEED_MS=39.7473
```

### 3. `evaluator.py`
Runs the pytest suite in a subprocess and returns an `EvaluationResult`:

- `correctness` — `1.0` only if every test passes (any failure zeroes the score)
- `speed_ms` — parsed from the `LOOPBENCH_SPEED_MS` marker
- `speed_score` — exponential decay of `speed_ms` (faster = higher)
- `combined_score` — `correctness * speed_score`, the primary fitness signal

The `combined_score` acts as a hard regression gate: a candidate that breaks any
test scores `0.0` and is rejected.

### 4. `loopbench.yaml`
Declares the target program, evaluator, sandbox command, the metric to optimize,
and the LLM settings.

> **Note — external repos need only two files.** These bundled examples include
> four files because they're self-contained. When you optimize *someone else's*
> repo (`loopbench init --job`), you write just **`loopbench.yaml` + `test_target.py`**:
> the target is the real repo file (no `initial_program.py`), and the sandbox
> scores your test directly (no `evaluator.py` — the test file *is* the evaluator).

## Writing your own example

1. Create a new directory under `examples/`.
2. Add `initial_program.py` with a single `EVOLVE-BLOCK` around the code to
   optimize and a fixed public entry point outside it.
3. Add a `test_*.py` suite that loads `LOOPBENCH_PROGRAM_PATH`, checks
   correctness, and prints `LOOPBENCH_SPEED_MS=<value>`.
4. Copy an `evaluator.py` and adjust the test file name and speed-score scale.
5. Add a `loopbench.yaml` pointing at your program, evaluator, and test command.

See the main [README](../README.md) for the full CLI reference and architecture.
