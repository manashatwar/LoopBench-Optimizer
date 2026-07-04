# LoopBench Optimizer — Documentation

Start with the [**5-minute Quick Start**](../QUICKSTART.md), then dive deeper here.

## Guides

| Document | Description |
|----------|-------------|
| [Defining Your Benchmark](defining-benchmarks.md) | Every way to score a run — speed markers, custom metrics, regex parsing, stdin/run mode, cost budgets, and optimizing an external repo |
| [Architecture](architecture/README.md) | Per-subsystem design references with diagrams (optimizer loop, ghost worktrees, repo context mapper, LLM editing, Docker sandbox, candidate database, search strategy) |

## Dashboard

| File | Description |
|------|-------------|
| [index.html](index.html) | Single-file dashboard (open directly, or serve locally / via GitHub Pages) |
| [data.json](data.json) | Latest exported run data (written automatically by every `loopbench run`) |

Every `loopbench run` writes `docs/data.json`. View the trajectory locally or
publish it:

```bash
# Local — open http://localhost:8080
python -m http.server 8080 --directory docs

# GitHub Pages — published at https://<user>.github.io/LoopBench-Optimizer/
git add docs/data.json && git commit -m "results" && git push
```

While a run is active, append `?refresh=5` to auto-refresh the dashboard every 5
seconds: `http://localhost:8080?refresh=5`.
