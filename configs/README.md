# Configuration Files

This directory contains configuration files for both the `optimizer` CLI (OptimizerLoop) and the underlying `openevolve` engine.

## OptimizerLoop Configs (use with `optimizer run`)

| File | Description |
|------|-------------|
| `loopbench_default.yaml` | Default template for `loopbench run` |
| `metric_parser_example.yaml` | Examples of metric extraction patterns |
| `early_stopping_example.yaml` | Early stopping with patience parameter |

### Generate a fresh template

```bash
optimizer init --output optimizer.yaml
```

This produces a fully-commented YAML with all 6 required sections:
`repository`, `llm`, `docker`, `database`, `metrics`, `search`.

### Example: Minimal optimizer config

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
    latency: 'Latency: ([\d.]+)ms'
  success_threshold: 0.10

search:
  strategy: "greedy"
  max_iterations: 50
  patience: 10
```

---

## OpenEvolve Engine Configs (use with `openevolve-run`)

| File | Description |
|------|-------------|
| `default_config.yaml` | All engine options with defaults |
| `island_config_example.yaml` | Island-based evolution setup |
| `island_examples.yaml` | Multiple island configurations |

### Island parameters

```yaml
database:
  num_islands: 5            # Separate populations
  migration_interval: 50    # Migrate every N generations
  migration_rate: 0.1       # Fraction to migrate (10%)
```

Guidelines:
- `num_islands`: 3–10 (more = more diversity)
- `migration_interval`: 25–100 (higher = more independence)
- `migration_rate`: 0.05–0.20 (higher = faster sharing)
