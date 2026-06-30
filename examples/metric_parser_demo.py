"""
Demonstration of the Metric Parser Engine

This script shows how to use the MetricParser to extract performance
metrics from various benchmark output formats.
"""

from openevolve.metric_parser import MetricParser, MetricPattern, create_parser_from_config


def demo_simple_latency():
    """Demo: Extract a simple latency metric"""
    print("=" * 70)
    print("Demo 1: Simple Latency Parsing")
    print("=" * 70)
    
    parser = MetricParser(
        patterns=[
            MetricPattern(
                name="latency",
                regex=r"Average latency: (\d+\.\d+)ms",
                goal="minimize",
                unit="ms"
            )
        ]
    )
    
    output = """
    Running benchmark...
    Test cases: 1000
    Average latency: 12.5ms
    Max latency: 25.3ms
    Benchmark complete.
    """
    
    metrics = parser.parse(output)
    print(f"Output:\n{output}")
    print(f"\nExtracted Metrics: {metrics}")
    print(f"  - latency: {metrics.get('latency', 'N/A')} ms")
    print(f"  - latency_score: {metrics.get('latency_score', 'N/A'):.4f}")
    print(f"  - combined_score: {metrics.get('combined_score', 'N/A'):.4f}")
    print()


def demo_multiple_metrics():
    """Demo: Extract multiple metrics"""
    print("=" * 70)
    print("Demo 2: Multiple Metrics (Latency + Throughput)")
    print("=" * 70)
    
    parser = MetricParser(
        patterns=[
            MetricPattern(
                name="latency",
                regex=r"Latency: (\d+\.\d+)ms",
                goal="minimize",
                unit="ms"
            ),
            MetricPattern(
                name="throughput",
                regex=r"Throughput: (\d+) req/s",
                goal="maximize",
                unit="req/s"
            ),
            MetricPattern(
                name="error_rate",
                regex=r"Error rate: (\d+\.\d+)%",
                goal="minimize",
                unit="%"
            )
        ],
        primary_metric="latency"
    )
    
    output = """
    Stress test results:
    Latency: 8.3ms
    Throughput: 5000 req/s
    Error rate: 0.12%
    Success rate: 99.88%
    """
    
    metrics = parser.parse(output)
    print(f"Output:\n{output}")
    print(f"\nExtracted Metrics: {metrics}")
    print(f"  - latency: {metrics.get('latency', 'N/A')} ms → score: {metrics.get('latency_score', 'N/A'):.4f}")
    print(f"  - throughput: {metrics.get('throughput', 'N/A')} req/s → score: {metrics.get('throughput_score', 'N/A'):.4f}")
    print(f"  - error_rate: {metrics.get('error_rate', 'N/A')}% → score: {metrics.get('error_rate_score', 'N/A'):.4f}")
    print(f"  - combined_score (based on latency): {metrics.get('combined_score', 'N/A'):.4f}")
    print()


def demo_pytest_benchmark():
    """Demo: Parse pytest-benchmark output"""
    print("=" * 70)
    print("Demo 3: Pytest Benchmark Format")
    print("=" * 70)
    
    parser = MetricParser(
        patterns=[
            MetricPattern(
                name="mean_time",
                regex=r"Mean:\s+(\d+\.\d+)\s+ms",
                goal="minimize",
                unit="ms"
            ),
            MetricPattern(
                name="stddev",
                regex=r"Std Dev:\s+(\d+\.\d+)\s+ms",
                goal="minimize",
                unit="ms"
            )
        ],
        primary_metric="mean_time"
    )
    
    output = """
    ============================= test session starts ==============================
    collected 5 items
    
    Benchmark report:
    Mean: 12.5 ms
    Std Dev: 1.2 ms
    Min: 10.8 ms
    Max: 15.3 ms
    
    ============================== 5 passed in 2.31s ===============================
    """
    
    metrics = parser.parse(output)
    print(f"Output:\n{output}")
    print(f"\nExtracted Metrics: {metrics}")
    print(f"  - mean_time: {metrics.get('mean_time', 'N/A')} ms")
    print(f"  - stddev: {metrics.get('stddev', 'N/A')} ms")
    print(f"  - combined_score: {metrics.get('combined_score', 'N/A'):.4f}")
    print()


def demo_scaling():
    """Demo: Unit conversion with scaling"""
    print("=" * 70)
    print("Demo 4: Unit Conversion (ms to seconds)")
    print("=" * 70)
    
    parser = MetricParser(
        patterns=[
            MetricPattern(
                name="execution_time",
                regex=r"Time: (\d+)ms",
                goal="minimize",
                unit="ms",
                scale=0.001  # Convert ms to seconds
            )
        ]
    )
    
    output = "Execution completed. Time: 1500ms"
    
    metrics = parser.parse(output)
    print(f"Output: {output}")
    print(f"\nExtracted Metrics: {metrics}")
    print(f"  - execution_time: {metrics.get('execution_time', 'N/A')} seconds (converted from 1500ms)")
    print(f"  - combined_score: {metrics.get('combined_score', 'N/A'):.4f}")
    print()


def demo_config_format():
    """Demo: Create parser from config dictionary"""
    print("=" * 70)
    print("Demo 5: Parser from Configuration")
    print("=" * 70)
    
    # Simple config format
    simple_config = {
        "regex": r"Average latency: (\d+\.\d+)ms",
        "goal": "minimize",
        "unit": "ms"
    }
    
    parser = create_parser_from_config(simple_config)
    output = "Test complete. Average latency: 15.7ms"
    metrics = parser.parse(output)
    
    print("Simple config format:")
    print(f"  {simple_config}")
    print(f"\nOutput: {output}")
    print(f"Extracted: {metrics}")
    print()
    
    # Advanced config format
    advanced_config = {
        "patterns": [
            {
                "name": "latency",
                "regex": r"Latency: (\d+\.\d+)ms",
                "goal": "minimize",
                "unit": "ms"
            },
            {
                "name": "throughput",
                "regex": r"Throughput: (\d+) ops/sec",
                "goal": "maximize",
                "unit": "ops/sec"
            }
        ],
        "primary_metric": "latency"
    }
    
    parser = create_parser_from_config(advanced_config)
    output = "Results: Latency: 8.5ms | Throughput: 3000 ops/sec"
    metrics = parser.parse(output)
    
    print("\nAdvanced config format:")
    print(f"  patterns: {len(advanced_config['patterns'])} metrics")
    print(f"  primary_metric: {advanced_config['primary_metric']}")
    print(f"\nOutput: {output}")
    print(f"Extracted: {metrics}")
    print()


def demo_error_handling():
    """Demo: Error handling when metrics not found"""
    print("=" * 70)
    print("Demo 6: Error Handling")
    print("=" * 70)
    
    parser = MetricParser(
        patterns=[
            MetricPattern(
                name="latency",
                regex=r"Latency: (\d+\.\d+)ms",
                goal="minimize"
            )
        ],
        fallback_score=0.0
    )
    
    # Output without the expected metric
    output = "Test completed successfully (no timing information)"
    
    metrics = parser.parse(output)
    print(f"Output: {output}")
    print(f"\nExtracted Metrics: {metrics}")
    print(f"  - error field present: {'error' in metrics}")
    print(f"  - combined_score (fallback): {metrics.get('combined_score', 'N/A')}")
    print()


def demo_real_world_fibonacci():
    """Demo: Real-world example from LoopBench fibonacci optimizer"""
    print("=" * 70)
    print("Demo 7: Real-World Example (Fibonacci Optimizer)")
    print("=" * 70)
    
    parser = MetricParser(
        patterns=[
            MetricPattern(
                name="execution_time",
                regex=r"Execution time for fib\(35\):\s+(\d+\.\d+)\s+seconds",
                goal="minimize",
                unit="seconds"
            )
        ]
    )
    
    output = """
    Testing fibonacci implementation...
    Calculating fib(35)...
    Execution time for fib(35): 1.234 seconds
    Result: 9227465
    Validation: PASS
    Test completed!
    """
    
    metrics = parser.parse(output)
    print(f"Output:\n{output}")
    print(f"\nExtracted Metrics: {metrics}")
    print(f"  - execution_time: {metrics.get('execution_time', 'N/A')} seconds")
    print(f"  - execution_time_score: {metrics.get('execution_time_score', 'N/A'):.4f}")
    print(f"  - combined_score: {metrics.get('combined_score', 'N/A'):.4f}")
    print()


def demo_comparison():
    """Demo: Compare minimize vs maximize goals"""
    print("=" * 70)
    print("Demo 8: Score Computation (Minimize vs Maximize)")
    print("=" * 70)
    
    # Minimize goal (lower is better)
    parser_min = MetricParser(
        patterns=[
            MetricPattern(
                name="metric",
                regex=r"Value: (\d+)",
                goal="minimize"
            )
        ]
    )
    
    # Maximize goal (higher is better)
    parser_max = MetricParser(
        patterns=[
            MetricPattern(
                name="metric",
                regex=r"Value: (\d+)",
                goal="maximize"
            )
        ]
    )
    
    test_values = [10, 50, 100]
    
    print("Testing with values:", test_values)
    print("\nMinimize goal (lower values → higher scores):")
    for val in test_values:
        metrics = parser_min.parse(f"Value: {val}")
        print(f"  Value: {val:3d} → Score: {metrics['metric_score']:.4f}")
    
    print("\nMaximize goal (higher values → higher scores):")
    for val in test_values:
        metrics = parser_max.parse(f"Value: {val}")
        print(f"  Value: {val:3d} → Score: {metrics['metric_score']:.4f}")
    print()


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print(" METRIC PARSER ENGINE - DEMONSTRATION")
    print("=" * 70 + "\n")
    
    demo_simple_latency()
    demo_multiple_metrics()
    demo_pytest_benchmark()
    demo_scaling()
    demo_config_format()
    demo_error_handling()
    demo_real_world_fibonacci()
    demo_comparison()
    
    print("=" * 70)
    print(" All demos completed!")
    print("=" * 70)
    print("\nNext steps:")
    print("  1. Review configs/metric_parser_example.yaml for configuration examples")
    print("  2. Integrate metric_parser into your evaluator config")
    print("  3. Run: python examples/metric_parser_demo.py")
    print()
