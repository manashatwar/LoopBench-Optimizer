# Quick Start — LoopBench Optimizer in 5 minutes

This walkthrough takes you from a fresh clone to a verified optimization,
end to end. It uses the built-in Fibonacci example as a fast smoke test.

## Prerequisite: Docker

LoopBench runs your tests inside an isolated Docker sandbox (`--network=none`,
read-only mount) so untrusted, evolved code can never touch your machine.

**Docker Desktop must be installed and running before you start.** Verify with:

```bash
docker info
```

If that prints server details (not an error), you're good to go.

---

## 1. Clone and install

```bash
git clone https://github.com/manashatwar/LoopBench-Optimizer.git
cd LoopBench-Optimizer

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e .
```

## 2. Set your LLM key

Copy the template and add your key. LoopBench works with any OpenAI-compatible
provider (Groq, Gemini, OpenAI):

```bash
cp .env.example .env
# then edit .env and paste your key
```

A minimal `.env` (Groq shown — free tier works):

```
GEMINI_API_KEY="gsk_your_key_here"
LLM_API_BASE="https://api.groq.com/openai/v1"
LLM_MODEL="llama-3.3-70b-versatile"
```

## 3. Run the smoke test

Point LoopBench at the Fibonacci example — a naive recursive function it will
evolve into a fast one:

```bash
loopbench run \
  --target . \
  --target-file examples/fibonacci_optimizer/initial_program.py \
  --metric latency \
  -i 3
```

You'll watch it establish a baseline, generate candidates with the LLM, run the
tests in the sandbox, and keep the best result. It finishes with a summary:

```
============================================================
✅  LoopBench run complete
============================================================
  Baseline score : 0.36xxxx
  Best score     : 0.99xxxx
  Improvement    : +179.xx%
------------------------------------------------------------
  Artifacts:
    Patch      : .../loopbench_output/best.patch
    Validation : .../loopbench_output/report/validation_report.md
    Dashboard  : .../docs/data.json
    Test log   : .../loopbench_output/test_log.txt
============================================================
```

## 4. Inspect the win

Four engineering-grade artifacts are produced in `loopbench_output/`:

| File | What to look for |
|------|------------------|
| `best.patch` | The clean, minimal unified diff — apply it with `git apply best.patch` |
| `report/validation_report.md` | Before/after metrics and patch status |
| `test_log.txt` | Proof every test still passed on the winning candidate |
| `../docs/data.json` | Data for the dashboard (step 5) |

```bash
cat loopbench_output/best.patch
cat loopbench_output/report/validation_report.md
```

Even when LoopBench rewrites a whole file internally for reliability, the patch
you get is surgical — only the lines that actually changed.

## 5. Visualize

The run already wrote `docs/data.json`. View the dashboard two ways:

**Local** — serve the `docs/` folder and open it:

```bash
python -m http.server 8080 --directory docs
# open http://localhost:8080
```

**GitHub Pages** — commit the data and view it publicly:

```bash
git add docs/data.json && git commit -m "add run results" && git push
# view at https://manashatwar.github.io/LoopBench-Optimizer/
```

---

## What next

- **Optimize a real repo:** `loopbench run --target https://github.com/user/repo --metric latency`
- **Try the second example:** swap the target file to
  `examples/prime_counter_optimizer/initial_program.py` (trial division → sieve)
- **Config-driven runs:** each example ships a `loopbench.yaml` you can run with
  `loopbench run --config examples/fibonacci_optimizer/loopbench.yaml`
- **Define your own benchmark:** see [Defining Your Benchmark](docs/defining-benchmarks.md)
  for the three ways to score a new file or repo (with full commands)
- **Full reference:** see the main [README](README.md) and
  [docs/architecture/](docs/architecture/README.md)
