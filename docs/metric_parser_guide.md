# Metric Parser Guide

The **Metric Parser Engine** provides flexible extraction of performance metrics from CLI output using regex patterns. This replaces hardcoded score extraction with configurable pattern matching, allowing you to work with any benchmark format.

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Configuration](#configuration)
4. [Usage Examples](#usage-examples)
5. [Common Patterns](#common-patterns)
6. [Advanced Features](#advanced-features)
7. [API Reference](#api-reference)
8. [Troubleshooting](#troubleshooting)

---

## Overview

### Problem
Benchmarks output results in various text formats:
```
Average latency: 12.5ms
Throughput: 1500 ops/sec
Execution time for fib(35): 1.234 seconds
```

Previously, you'd need to write custom parsing code for each format. The metric parser provides a unified, configuration-based solution.

### Solution
Define regex patterns in your YAML config:
```yaml
evaluator:
  metric_parser:
    regex: "Average latency: (\\d+\\.\\d+)ms"
    goal: "minimize"
```

The parser extracts metrics and computes normalized scores automatically.

---

## Quick Start

### 1. Add to Config

Add a `metric_parser` section to your `evaluator` config:

```yaml
evaluator:
  timeout: 120
  metric_parser:
    regex: "Execution time: (\\d+\\.\\d+)s"
    goal: "minimize"
    unit: "seconds"
    fallback_score: 0.0
```

### 2. Use in Evaluator

The metric parser integrates automatically with the evaluator. Call `parse_cli_output()` to extract metrics:

```python
from openevolve.evaluator import Evaluator

# Evaluator automatically loads metric_parser from config
evaluator = Evaluator(config, evaluation_file)

# Parse CLI output
output = "Test completed. Execution time: 1.234s"
metrics = evaluator.parse_cli_output(output)

# Returns: {"metric": 1.234, "metric_score": 0.96, "combined_score": 0.96}
```

### 3. Run Your Benchmark

```bash
optimizer run --config your_config.yaml
```

The parser extracts metrics from your benchmark output automatically.

---

## Configuration

### Simple Format (Single Metric)

For benchmarks with a single performance metric:

```yaml
evaluator:
  metric_parser:
    regex: "Average latency: (\\d+\\.\\d+)ms"
    goal: "minimize"      # or "maximize"
    unit: "ms"            # optional: for logging
    fallback_score: 0.0   # score if parsing fails
    name: "latency"       # optional: metric name (default: "metric")
```

**Important:** The regex must contain exactly **one capture group** `(...)` to extract the numeric value.

### Advanced Format (Multiple Metrics)

For benchmarks that output multiple metrics:

```yaml
evaluator:
  metric_parser:
    patterns:
      - name: "latency"
        regex: "Latency: (\\d+\\.\\d+)ms"
        goal: "minimize"
        unit: "ms"
      
      - name: "throughput"
        regex: "Throughput: (\\d+) ops/sec"
        goal: "maximize"
        unit: "ops/sec"
      
      - name: "memory"
        regex: "Peak memory: (\\d+)MB"
        goal: "minimize"
        unit: "MB"
    
    primary_metric: "latency"  # which metric to use for combined_score
    fallback_score: 0.0
    multiline: true           # enable multiline regex matching
    ignore_case: false        # case-sensitive by default
```

### Configuration Options

| Option | Type | Description | Default |
|--------|------|-------------|---------|
| `regex` | string | Regex pattern with one capture group (simple format) | Required |
| `goal` | string | "minimize" or "maximize" | Required |
| `unit` | string | Unit suffix for logging (e.g., "ms", "ops/sec") | `None` |
| `scale` | float | Scaling factor (e.g., 0.001 to convert ms→s) | `1.0` |
| `name` | string | Metric name | `"metric"` |
| `patterns` | list | List of metric patterns (advanced format) | `None` |
| `primary_metric` | string | Which metric to use for combined_score | First pattern |
| `fallback_score` | float | Score when parsing fails | `0.0` |
| `multiline` | bool | Enable multiline regex matching | `true` |
| `ignore_case` | bool | Case-insensitive matching | `false` |

---

## Usage Examples

### Example 1: Pytest Benchmark

**Output:**
```
============================= test session starts ==============================
Mean: 12.5 ms
Std Dev: 1.2 ms
============================== 5 passed in 2.31s ===============================
```

**Config:**
```yaml
evaluator:
  metric_parser:
    patterns:
      - name: "mean_time"
        regex: "Mean: (\\d+\\.\\d+) ms"
        goal: "minimize"
        unit: "ms"
      - name: "stddev"
        regex: "Std Dev: (\\d+\\.\\d+) ms"
        goal: "minimize"
        unit: "ms"
    primary_metric: "mean_time"
```

### Example 2: Custom Benchmark

**Output:**
```
Running benchmark...
Execution time: 2.45s
Peak memory: 128MB
Benchmark complete.
```

**Config:**
```yaml
evaluator:
  metric_parser:
    patterns:
      - name: "execution_time"
        regex: "Execution time: (\\d+\\.\\d+)s"
        goal: "minimize"
      - name: "memory_usage"
        regex: "Peak memory: (\\d+)MB"
        goal: "minimize"
    primary_metric: "execution_time"
```

### Example 3: Throughput Benchmark

**Output:**
```
Benchmark results:
Throughput: 1500 ops/sec
Completed.
```

**Config:**
```yaml
evaluator:
  metric_parser:
    regex: "Throughput: (\\d+) ops/sec"
    goal: "maximize"
    unit: "ops/sec"
```

### Example 4: Fibonacci Optimizer

**Output:**
```
Testing fibonacci implementation...
Execution time for fib(35): 1.234 seconds
Result: 9227465
Test passed!
```

**Config:**
```yaml
evaluator:
  metric_parser:
    regex: "Execution time for fib\\(35\\): (\\d+\\.\\d+) seconds"
    goal: "minimize"
    unit: "seconds"
```

---

## Common Patterns

### Time Measurements

```yaml
# Seconds
regex: "Time: (\\d+\\.\\d+)s"

# Milliseconds
regex: "Latency: (\\d+\\.\\d+)ms"

# Microseconds
regex: "Duration: (\\d+\\.\\d+)us"

# Convert ms to seconds with scaling
regex: "Time: (\\d+)ms"
scale: 0.001
```

### Throughput

```yaml
# Operations per second
regex: "Throughput: (\\d+) ops/sec"
goal: "maximize"

# Requests per second
regex: "(\\d+) requests/sec"
goal: "maximize"
```

### Memory

```yaml
# Megabytes
regex: "Peak memory: (\\d+\\.\\d+)MB"
goal: "minimize"

# Kilobytes
regex: "Memory usage: (\\d+)KB"
goal: "minimize"
```

### Accuracy/Error Rate

```yaml
# Accuracy (higher is better)
regex: "Accuracy: (\\d+\\.\\d+)%"
goal: "maximize"

# Error rate (lower is better)
regex: "Error rate: (\\d+\\.\\d+)%"
goal: "minimize"
```

### JSON Output

```yaml
# Extract from JSON key
regex: "\"latency\": (\\d+\\.\\d+)"
goal: "minimize"
```

---

## Advanced Features

### Unit Conversion with Scaling

Convert units using the `scale` parameter:

```yaml
evaluator:
  metric_parser:
    regex: "Time: (\\d+)ms"
    goal: "minimize"
    scale: 0.001  # Convert milliseconds to seconds
```

Input: `"Time: 500ms"` → Output: `{"metric": 0.5, ...}`

### Multiple Metrics with Primary Score

Extract multiple metrics but optimize for one:

```yaml
evaluator:
  metric_parser:
    patterns:
      - name: "latency"
        regex: "Latency: (\\d+\\.\\d+)ms"
        goal: "minimize"
      - name: "throughput"
        regex: "Throughput: (\\d+) ops/sec"
        goal: "maximize"
    primary_metric: "latency"  # combined_score uses latency
```

### Case-Insensitive Matching

Match patterns regardless of case:

```yaml
evaluator:
  metric_parser:
    regex: "average latency: (\\d+\\.\\d+)ms"
    goal: "minimize"
    ignore_case: true  # Matches "AVERAGE LATENCY", "Average Latency", etc.
```

### Multiline Patterns

Match across multiple lines:

```yaml
evaluator:
  metric_parser:
    regex: "Results:\\s+Score: (\\d+\\.\\d+)"
    goal: "maximize"
    multiline: true
```

### Fallback Scores

Specify a fallback score when parsing fails:

```yaml
evaluator:
  metric_parser:
    regex: "Score: (\\d+\\.\\d+)"
    goal: "maximize"
    fallback_score: 0.1  # Use 0.1 if parsing fails
```

### Metric patterns in optimizer config

Add a `metric_patterns` list to your `optimizer.yaml`:

```yaml
metrics:
  patterns:
    latency: 'Latency: ([\d.]+)ms'
    throughput: 'Throughput: ([\d.]+) ops/sec'
  success_threshold: 0.10
```

Or pass patterns directly to `OptimizerLoop`:

```python
from openevolve.optimizer_loop import OptimizerLoop

loop = OptimizerLoop({
    "metric_patterns": [
        {"name": "latency", "regex": r"Latency: ([\d.]+)ms", "goal": "minimize"},
        {"name": "throughput", "regex": r"Throughput: ([\d.]+)", "goal": "maximize"},
    ],
    ...
})
```

---

## API Reference

### MetricParser Class

```python
from openevolve.metric_parser import MetricParser, MetricPattern

# Create parser
parser = MetricParser(
    patterns=[
        MetricPattern(
            name="latency",
            regex=r"Latency: (\d+\.\d+)ms",
            goal="minimize",
            unit="ms"
        )
    ],
    primary_metric="latency",
    fallback_score=0.0
)

# Parse output
output = "Test completed. Latency: 12.5ms"
metrics = parser.parse(output)
# Returns: {"latency": 12.5, "latency_score": 0.92, "combined_score": 0.92}
```

### MetricPattern Dataclass

```python
from openevolve.metric_parser import MetricPattern

pattern = MetricPattern(
    name="latency",              # Metric name
    regex=r"Latency: (\d+\.\d+)", # Regex with one capture group
    goal="minimize",             # "minimize" or "maximize"
    unit="ms",                   # Optional unit for logging
    scale=1.0                    # Optional scaling factor
)
```

### create_parser_from_config Function

```python
from openevolve.metric_parser import create_parser_from_config

config = {
    "regex": r"Time: (\d+\.\d+)s",
    "goal": "minimize",
    "unit": "seconds"
}

parser = create_parser_from_config(config)
```

### Evaluator Integration

```python
from openevolve.evaluator import Evaluator

evaluator = Evaluator(config, evaluation_file)

# Parse CLI output
output = subprocess.run(["pytest"], capture_output=True, text=True)
metrics = evaluator.parse_cli_output(output.stdout)

# Use metrics in evaluation
if metrics:
    score = metrics.get("combined_score", 0.0)
```

---

## Troubleshooting

### Regex Not Matching

**Problem:** Parser returns `{"error": 0.0, "combined_score": 0.0}`

**Solutions:**

1. **Test your regex online** at [regex101.com](https://regex101.com)
   - Select Python flavor
   - Paste your output as test string
   - Ensure pattern has exactly one capture group

2. **Enable debug logging:**
   ```yaml
   log_level: "DEBUG"
   ```
   Check logs for "Metric 'X' not found in output"

3. **Common issues:**
   - Missing escape: `\d` needs to be `\\d` in YAML
   - No capture group: `\d+` should be `(\d+)`
   - Too many capture groups: `(\d+)\.(\d+)` has 2 groups (invalid)
   - Wrong flags: Add `multiline: true` or `ignore_case: true`

### Invalid Numeric Values

**Problem:** Parser returns error despite matching the pattern

**Solutions:**

1. **Check captured value is numeric:**
   ```python
   import re
   match = re.search(r"Time: (\d+\.\d+)", output)
   if match:
       print(f"Captured: '{match.group(1)}'")  # Should be "12.5", not "12.5ms"
   ```

2. **Ensure capture group contains only the number:**
   - Bad: `"Time: (\d+\.\d+ms)"` captures "12.5ms"
   - Good: `"Time: (\d+\.\d+)ms"` captures "12.5"

### Multiple Capture Groups Error

**Problem:** `ValueError: must contain exactly one capture group`

**Solutions:**

Use **non-capturing groups** `(?:...)` for grouping without capturing:
```yaml
# Bad (2 capture groups)
regex: "Time: (\d+)m(\d+)s"

# Good (1 capture group)
regex: "Time: (?:\d+m)?(\d+)s"  # Captures only seconds

# Alternative: Capture total in one unit
regex: "Time: (\d+\.\d+)s"  # Convert minutes to seconds beforehand
```

### Parsing Fails Silently

**Problem:** No error but metrics are empty

**Solutions:**

1. **Check if metric_parser is configured:**
   ```python
   print(evaluator.metric_parser)  # Should not be None
   ```

2. **Verify config loading:**
   ```yaml
   evaluator:
     metric_parser:  # Correct indentation
       regex: "..."
   ```

3. **Check output is being captured:**
   ```python
   print(f"Output length: {len(output)}")
   print(f"Output sample: {output[:200]}")
   ```

### Score Normalization Issues

**Problem:** Scores don't reflect performance accurately

**Solutions:**

The default normalization uses heuristic formulas. For custom normalization:

1. **Adjust the reference value** by subclassing MetricParser:
   ```python
   class CustomParser(MetricParser):
       def _compute_score(self, value: float, goal: str) -> float:
           if goal == "minimize":
               # Custom normalization for your metric range
               return max(0.0, min(1.0, 1.0 - (value / 100.0)))
           else:
               return max(0.0, min(1.0, value / 1000.0))
   ```

2. **Use scaling** to normalize values:
   ```yaml
   metric_parser:
     regex: "Time: (\\d+)ms"
     scale: 0.001  # Convert to seconds
     goal: "minimize"
   ```

### Config Not Loading

**Problem:** `metric_parser` config not being read

**Solutions:**

1. **Check YAML syntax:**
   ```bash
   python -c "import yaml; yaml.safe_load(open('config.yaml'))"
   ```

2. **Verify config path:**
   ```bash
   loopbench run --config path/to/config.yaml
   ```

3. **Check evaluator section exists:**
   ```yaml
   evaluator:
     timeout: 120
     metric_parser:  # Must be under evaluator
       regex: "..."
   ```

---

## Best Practices

1. **Test regex patterns** on real output samples before deploying
2. **Use descriptive metric names** (e.g., "latency" not "metric1")
3. **Specify units** for clarity in logs
4. **Set appropriate fallback_score** (0.0 for minimize, 1.0 for maximize)
5. **Log sample output** during development to debug parsing issues
6. **Use multiline: true** for complex output formats
7. **Keep patterns simple** - avoid complex lookaheads/lookbehinds
8. **Document your regex** in config comments for team members

---

## Examples in Repository

See working examples:

- **Simple:** `configs/metric_parser_example.yaml`
- **Demo script:** `examples/metric_parser_demo.py`
- **Tests:** `tests/test_metric_parser.py`

Run tests:
```bash
pytest tests/test_metric_parser.py -v
```

---

## Support

For issues or questions:
- Check [troubleshooting](#troubleshooting) section
- Review [test examples](../tests/test_metric_parser.py)
- Open an issue on GitHub
