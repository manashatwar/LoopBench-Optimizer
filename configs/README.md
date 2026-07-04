# Configuration Files

| File | Used by | Description |
|------|---------|-------------|
| `loopbench_default.yaml` | `loopbench init --name` | The template copied when you scaffold a new project. Current LoopBench schema: `target` / `sandbox` / `metric` / `search` / `constraints`. |
| `default_config.yaml` | `openevolve-run` / `optimizer` | Full reference of the underlying OpenEvolve engine options (population, islands, MAP-Elites, evaluator). Advanced use only. |

## Scaffold a project

```bash
loopbench init --name my_project     # writes my_project.yaml from loopbench_default.yaml
```

Then edit the two paths under `target` (the file to optimize + your test), and run:

```bash
loopbench check --config my_project.yaml    # validate + dry-run the test
loopbench run   --config my_project.yaml    # optimize
```

Optimizing an external repo instead? Use `loopbench init --job my_job` — it
scaffolds a job folder (`loopbench.yaml` + `test_target.py`) that points at a
repo without editing it.

For every scoring option (speed markers, custom metrics, regex parsing,
stdin/run mode, cost budgets, search strategy), see
[**Defining Your Benchmark**](../docs/defining-benchmarks.md).
