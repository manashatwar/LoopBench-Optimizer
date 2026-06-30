# LoopBench Optimizer

<div align="center">

<img src="openevolve-logo.png" alt="LoopBench Optimizer Logo" width="400">

**⚡ Autonomous evolutionary code optimization — powered by LLMs**

*Point it at any GitHub repository. Watch it evolve your code to run faster.*

[![License](https://img.shields.io/github/license/algorithmicsuperintelligence/openevolve)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-457%20passing-brightgreen)](tests/)

</div>

---

## What It Does

LoopBench Optimizer runs a closed-loop, multi-generation optimization cycle:

1. **Map** — builds an LLM-ready context map of your repository
2. **Generate** — uses an LLM to produce a unified diff patch
3. **Apply** — applies the patch to an isolated git worktree
4. **Test** — runs your test suite inside a Docker sandbox
5. **Extract** — parses performance metrics from test output
6. **Record** — stores the attempt in a SQLite audit database
7. **Select** — picks the best candidate as the next baseline

Each generation learns from previous failures, compounding improvements over time.

---

## Quick Start

```bash
# Install
pip install -e .

# Generate a config template
optimizer init --output optimizer.yaml

# Edit optimizer.yaml (set your repo URL, API key, etc.)

# Run optimization
optimizer run --config optimizer.yaml

# View results in the dashboard
optimizer dashboard --run-id <id> --open
```

---

## Project Structure

```
LoopBench-Optimizer/
│
├── openevolve/                  # Core library (extended from OpenEvolve fork)
│   ├── cli.py                   # optimizer CLI entry point (init/run/resume/export/dashboard)
│   ├── optimizer_loop.py        # 7-phase orchestrator
│   ├── search_strategy.py       # GreedySearch, BeamSearch, RandomRestartSearch
│   ├── repo_manager.py          # clone_repository, detect_language, detect_test_framework
│   ├── config_validator.py      # validate_optimizer_config, generate_template
│   ├── report_generator.py      # FinalReportWriter (patch, validation, README, PR)
│   ├── database.py              # CandidateDatabase with SQLite audit trail
│   ├── metric_parser.py         # MetricParser (regex + JSON patterns)
│   ├── workspace_manager.py     # git worktree isolation
│   ├── llm/                     # LLM providers (OpenAI, Anthropic, Ollama)
│   │   ├── base.py              # extract_patch_from_response, retry logic
│   │   └── ensemble.py          # generate_patch with exponential backoff
│   └── repo_mapper/             # repository-to-context mapper
│       ├── mapper.py            # RepoContextMapper
│       └── optimizer_prompt.py  # create_optimizer_prompt (baseline + failure history)
│
├── sandbox/                     # Docker sandbox execution
│   ├── runner.py                # run_in_sandbox, verify_output_streams
│   ├── entrypoint.sh            # container entrypoint
│   └── Dockerfile.sandbox       # test execution image
│
├── loopbench/                   # LoopBench CLI (legacy evaluator-first interface)
│   └── cli.py                   # loopbench run/init/check
│
├── docs/                        # Static GitHub Pages dashboard
│   └── index.html               # Single-file React dashboard (no build step)
│
├── configs/                     # Example configuration files
│   ├── default_config.yaml
│   └── loopbench_default.yaml
│
├── examples/                    # Example optimization problems
│   └── ...
│
├── tests/                       # Test suite (457 tests)
│   ├── property/                # Hypothesis property-based tests (Properties 1–9)
│   ├── integration/
│   ├── test_optimizer_loop*.py  # OptimizerLoop tests
│   ├── test_search_strategy.py
│   ├── test_config_validator.py
│   ├── test_report_generator.py
│   ├── test_optimizer_cli.py
│   ├── test_audit_trail.py
│   ├── test_dashboard.py
│   ├── test_repo_manager.py
│   └── test_end_to_end.py
│
├── pyproject.toml               # Package config + entry points
├── Makefile                     # Common dev commands
└── LICENSE
```

---

## CLI Commands

```bash
# Generate a config template (all 6 required sections)
optimizer init --output optimizer.yaml

# Run an optimization
optimizer run --config optimizer.yaml --max-iterations 50 --output results/

# Resume an interrupted run
optimizer resume --run-id <id> --db optimizer.db

# Export run data
optimizer export --run-id <id> --format json
optimizer export --run-id <id> --format markdown

# Launch the dashboard
optimizer dashboard --run-id <id> --open          # local server + browser
optimizer dashboard --run-id <id> --no-server     # generate docs/data.json only
```

---

## Dashboard

The dashboard runs in two modes:

**GitHub Pages (static)** — commit `docs/data.json` to share results publicly:
```bash
optimizer dashboard --run-id <id> --no-server
git add docs/data.json && git push
# View at: https://your-org.github.io/LoopBench-Optimizer/
```

**Local live server** — monitor an active run in real time:
```bash
optimizer dashboard --run-id <id> --port 8080 --open
# Auto-refreshes every N seconds via ?refresh=N
```

---

## Configuration

The optimizer requires a YAML config with exactly 6 sections:

```yaml
repository:
  url: "https://github.com/your-org/repo.git"
  target_files: ["src/main.py"]
  auth_token: "${GITHUB_TOKEN}"

llm:
  provider: "openai"
  model: "gpt-4"
  api_key: "${OPENAI_API_KEY}"

docker:
  test_command: "pytest --benchmark-only -v"
  timeout: 300

database:
  path: "./optimizer.db"

metrics:
  patterns:
    execution_time: 'Mean: ([\d.]+) seconds'
  success_threshold: 0.10

search:
  strategy: "greedy"   # greedy | beam | random_restart
  max_iterations: 50
  patience: 10
```

Generate a full template with: `optimizer init`

---

## Search Strategies

| Strategy | Description | Parallelizable |
|----------|-------------|---------------|
| `greedy` | Always use the single best candidate | No |
| `beam` | Top-K random selection (`beam_width`) | Yes |
| `random_restart` | Periodically revert to baseline (`restart_interval`) | No |

---

## Running Tests

```bash
# Full test suite
pytest tests/ -v

# Property-based tests only
pytest tests/property/ -v

# End-to-end tests
pytest tests/test_end_to_end.py -v
```

---

## Architecture

Built on top of the [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) evolutionary coding agent. The fork adds:

- **Repository-level optimization** (full repo context, not just single files)
- **Git worktree isolation** per generation
- **7-phase orchestration** with explicit phase tracking
- **SQLite audit trail** with full lineage
- **GitHub Pages dashboard** (no server required for sharing)
- **`optimizer` CLI** with init/run/resume/export/dashboard commands

---

## License

MIT — see [LICENSE](LICENSE).
