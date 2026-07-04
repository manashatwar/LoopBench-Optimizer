# LoopBench Examples

This directory contains runnable optimization examples for LoopBench Optimizer.
Each one is a small, self-contained project you can point the optimizer at to
watch it evolve faster code while keeping every test green.

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

Each optimizer example is **three files**:

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

### 3. `loopbench.yaml`
Points at the file to optimize (`target.file`), the test that scores it
(`target.evaluator`), the sandbox command, any pip deps, the metric, and
constraints:

```yaml
target:
  file: initial_program.py
  evaluator: test_prime_counter.py
sandbox:
  command: "pytest test_prime_counter.py -v -s -q"
  # pip: ["numpy"]        # only if the code needs third-party packages
metric:      { name: "combined_score", threshold: 0.95 }
constraints: { max_iterations: 5 }
```

> **How scoring works — there is no `evaluator.py`.** The sandbox runs your
> `test_*.py` directly and computes the score itself: correctness from pass/fail,
> speed from the `LOOPBENCH_SPEED_MS` line
> (`combined_score = correctness × speed_score`). A candidate that breaks any
> test scores `0.0` and is rejected. **The test file *is* the evaluator.**
>
> **External repos** need only two files — `loopbench.yaml` + `test_target.py`
> (no `initial_program.py`, since the target is the real repo file). Scaffold one
> with `loopbench init --job`.

## Writing your own example

1. Create a new directory under `examples/`.
2. Add `initial_program.py` with a single `EVOLVE-BLOCK` around the code to
   optimize and a fixed public entry point outside it.
3. Add a `test_*.py` suite that loads `LOOPBENCH_PROGRAM_PATH`, checks
   correctness, and prints `LOOPBENCH_SPEED_MS=<value>`.
4. Add a `loopbench.yaml` with `target.file`, `target.evaluator` (the test),
   `sandbox.command`, and (if the code needs packages) `sandbox.pip`.

See the main [README](../README.md) for the full CLI reference and architecture.
