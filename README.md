<div align="center">

# ⚡ LoopBench Optimizer

**Autonomous, evaluator-driven code optimization — powered by LLMs.**

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](pyproject.toml)
[![Docker](https://img.shields.io/badge/Docker-required-orange)](https://www.docker.com)
[![CI](https://github.com/manashatwar/LoopBench-Optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/manashatwar/LoopBench-Optimizer/actions/workflows/ci.yml)

</div>

LoopBench points an LLM at your slow code, runs each attempt in a sandboxed loop,
and evolves the file until it's faster — while **guaranteeing** the final patch
still passes every test. LLMs happily write faster code that quietly breaks
things; LoopBench makes speed *and* correctness a hard gate, so you only ever get
a verified diff.

---

## See it

```python
# BEFORE — O(2ⁿ) exponential recursion
def fibonacci(n):
    if n <= 0: return 0
    if n == 1: return 1
    return fibonacci(n - 1) + fibonacci(n - 2)
```

```python
# AFTER — memoized, O(n), evolved by LoopBench
def fibonacci(n, _memo={0: 0, 1: 1}):
    if n in _memo: return _memo[n]
    _memo[n] = fibonacci(n - 1) + fibonacci(n - 2)
    return _memo[n]
```

On the bundled Fibonacci smoke test this lifts the score from **0.36 → 0.99** in
a few generations — every candidate re-verified against the test suite before it
counts. See [`examples/`](examples/) for reproducible runs on primes, JSON
parsing, palindromes, and NumPy vectorization.

> _A recorded `loopbench run` terminal cast will live here — coming soon._

---

## Quick Start

Docker Desktop must be running (tests execute in an isolated sandbox).

```bash
pip install -e .
cp .env.example .env        # add your LLM key (any OpenAI-compatible provider):
#   GEMINI_API_KEY="..."                              # Groq, Gemini, OpenAI, …
#   LLM_API_BASE="https://api.groq.com/openai/v1"
#   LLM_MODEL="llama-3.3-70b-versatile"

# Optimize a file that already has a timing test:
loopbench run --target . --target-file examples/fibonacci_optimizer/initial_program.py --metric latency -i 5
```

Minutes later you get a verified `loopbench_output/best.patch`, a validation
report, a test log, and dashboard data. The full walkthrough is in the
[**5-minute Quick Start**](QUICKSTART.md).

**Optimizing someone else's repo?** Scaffold a job folder, edit two files, run —
the target repo stays untouched:

```bash
loopbench init --job my_job                 # creates my_job/loopbench.yaml + test_target.py
loopbench run --config my_job/loopbench.yaml
```

See [**Defining Your Benchmark**](docs/defining-benchmarks.md) for the full
config, dependencies, cost/runtime budgets, custom commands, and stdin/run mode.

---

## Key Features

- **Closed-loop evolution** — multi-generation optimization that learns from each
  failure, compounding improvements over time.
- **Verified patches only** — correctness is a hard gate; any failing test scores
  `0.0`. You never receive a diff that breaks behavior.
- **Zero-corrupt patches** — the LLM edits via full-rewrite or search/replace
  blocks (`auto`-routed by file size); the `.patch` is always computed with
  `difflib`, so it's guaranteed to apply.
- **Safe sandboxing** — every candidate runs in Docker with `--network=none` and
  a read-only mount. Bring any runner via `--test-command` (pytest, benchmarks,
  type checks, plain scripts).
- **Repository-aware** — maps whole-repo context for the LLM, not just the single
  file. Third-party deps are auto-detected and installed into a cached image.
- **Bounded & audit-ready** — stop on a token, dollar, runtime, or iteration
  budget; every attempt, prompt, and metric is recorded in a SQLite audit trail.

Provider-agnostic via `LLM_API_BASE` / `LLM_MODEL` (Groq, Gemini, OpenAI, …).

---

## How It Works

Each generation runs a closed loop:

1. **Map** — build an LLM-ready context map of the repository
2. **Generate** — ask the LLM to improve the target file
3. **Apply** — apply the edit in an isolated git worktree, compute a valid `.patch`
4. **Test** — run the suite in a Docker sandbox (`--network=none`, read-only)
5. **Extract** — parse performance metrics from test output
6. **Record** — store the attempt in a SQLite audit database
7. **Select** — pick the best candidate as the next baseline

For how each subsystem works — with diagrams — see
[**`docs/architecture/`**](docs/architecture/README.md).

---

## Try the Demos

Each folder in [`examples/`](examples/) is a self-contained optimization you can
run directly:

| Demo | What it shows |
|------|---------------|
| [`fibonacci_optimizer/`](examples/fibonacci_optimizer/) | Hello-world: naive recursion → memoized |
| [`prime_counter_optimizer/`](examples/prime_counter_optimizer/) | Trial division → Sieve of Eratosthenes |
| [`numpy_vectorize_optimizer/`](examples/numpy_vectorize_optimizer/) | Auto-installs NumPy, then vectorizes a Python loop |
| [`stdin_palindrome/`](examples/stdin_palindrome/) | Run mode: optimize a stdin/stdout script (no importable tests) |

```bash
loopbench run --target . --target-file examples/prime_counter_optimizer/initial_program.py --metric latency -i 5
```

---

## Documentation

- [**Quick Start**](QUICKSTART.md) — clone to a verified optimization in 5 minutes
- [**Defining Your Benchmark**](docs/defining-benchmarks.md) — every scoring mode + full CLI flags
- [**Architecture**](docs/architecture/README.md) — per-subsystem design with diagrams
- [**Contributing**](CONTRIBUTING.md) — dev setup, repository layout, running tests
- [**Dashboard**](docs/README.md) — view a run's trajectory locally or on GitHub Pages

---

## Advanced

LoopBench is a fork of [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve).
The separate `optimizer` CLI runs the **same** LLM + evaluator loop with a
heavier search strategy (MAP-Elites / island populations) for very hard,
open-ended problems — most users won't need it. The everyday `loopbench run`
flow above is just as capable for "make this faster and keep tests green."

---

## License

Apache-2.0 — see [LICENSE](LICENSE). LoopBench Optimizer is a fork of
[OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve), also
licensed under Apache-2.0.
