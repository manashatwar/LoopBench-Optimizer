#!/usr/bin/env python3
"""
Demonstration of Metric Parser Integration with Evaluator

This example shows how the metric parser integrates with the evaluator
to extract performance metrics from CLI output automatically.
"""

import tempfile
import os
from openevolve.config import EvaluatorConfig
from openevolve.evaluator import Evaluator


def demo_simple_metric_extraction():
    """Demo 1: Simple metric extraction from CLI output"""
    print("\n" + "="*70)
    print("DEMO 1: Simple Metric Extraction")
    print("="*70)
    
    # Configure evaluator with metric parser
    config = EvaluatorConfig(
        timeout=60,
        metric_parser={
            "regex": r"Execution time: (\d+\.\d+)s",
            "goal": "minimize",
            "unit": "seconds"
        }
    )
    
    # Create a minimal evaluator
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("def evaluate(path): return {'combined_score': 1.0}")
        eval_file = f.name
    
    try:
        evaluator = Evaluator(config, eval_file)
        
        # Simulate CLI output from a benchmark
        cli_output = """
Running benchmark...
Execution time: 2.45s
Test completed successfully
"""
        
        print("\nCLI Output:")
        print(cli_output)
        
        # Parse metrics
        metrics = evaluator.parse_cli_output(cli_output)
        
        print("\nExtracted Metrics:")
        for key, value in metrics.items():
            print(f"  {key}: {value}")
        
        print(f"\n✓ Successfully extracted metric: {metrics.get('metric')}s")
        print(f"✓ Normalized score: {metrics.get('combined_score'):.4f}")
        
    finally:
        os.unlink(eval_file)


def demo_multiple_metrics():
    """Demo 2: Multiple metric extraction"""
    print("\n" + "="*70)
    print("DEMO 2: Multiple Metrics Extraction")
    print("="*70)
    
    config = EvaluatorConfig(
        metric_parser={
            "patterns": [
                {
                    "name": "latency",
                    "regex": r"Average latency: (\d+\.\d+)ms",
                    "goal": "minimize",
                    "unit": "ms"
                },
                {
                    "name": "throughput",
                    "regex": r"Throughput: (\d+) req/s",
                    "goal": "maximize",
                    "unit": "req/s"
                },
                {
                    "name": "memory",
                    "regex": r"Peak memory: (\d+)MB",
                    "goal": "minimize",
                    "unit": "MB"
                }
            ],
            "primary_metric": "latency"
        }
    )
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("def evaluate(path): return {'combined_score': 1.0}")
        eval_file = f.name
    
    try:
        evaluator = Evaluator(config, eval_file)
        
        cli_output = """
Benchmark Results:
Average latency: 15.5ms
Throughput: 2500 req/s
Peak memory: 128MB
Test completed
"""
        
        print("\nCLI Output:")
        print(cli_output)
        
        metrics = evaluator.parse_cli_output(cli_output)
        
        print("\nExtracted Metrics:")
        print(f"  Latency: {metrics.get('latency')}ms (score: {metrics.get('latency_score', 0):.4f})")
        print(f"  Throughput: {metrics.get('throughput')} req/s (score: {metrics.get('throughput_score', 0):.4f})")
        print(f"  Memory: {metrics.get('memory')}MB (score: {metrics.get('memory_score', 0):.4f})")
        print(f"\n✓ Combined Score (based on primary metric 'latency'): {metrics.get('combined_score'):.4f}")
        
    finally:
        os.unlink(eval_file)


def demo_pytest_benchmark():
    """Demo 3: Real pytest benchmark output"""
    print("\n" + "="*70)
    print("DEMO 3: Pytest Benchmark Output")
    print("="*70)
    
    config = EvaluatorConfig(
        metric_parser={
            "patterns": [
                {
                    "name": "mean_time",
                    "regex": r"Mean: (\d+\.\d+) ms",
                    "goal": "minimize",
                    "unit": "ms"
                },
                {
                    "name": "stddev",
                    "regex": r"Std Dev: (\d+\.\d+) ms",
                    "goal": "minimize",
                    "unit": "ms"
                }
            ],
            "primary_metric": "mean_time"
        }
    )
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("def evaluate(path): return {'combined_score': 1.0}")
        eval_file = f.name
    
    try:
        evaluator = Evaluator(config, eval_file)
        
        cli_output = """
============================= test session starts ==============================
collected 5 items

Benchmark report:
Mean: 12.5 ms
Std Dev: 1.2 ms
Min: 10.8 ms
Max: 15.3 ms

============================== 5 passed in 2.31s ===============================
"""
        
        print("\nCLI Output:")
        print(cli_output)
        
        metrics = evaluator.parse_cli_output(cli_output)
        
        print("\nExtracted Metrics:")
        print(f"  Mean time: {metrics.get('mean_time')}ms")
        print(f"  Standard deviation: {metrics.get('stddev')}ms")
        print(f"  Combined score: {metrics.get('combined_score'):.4f}")
        
    finally:
        os.unlink(eval_file)


def demo_error_handling():
    """Demo 4: Error handling when metric not found"""
    print("\n" + "="*70)
    print("DEMO 4: Error Handling")
    print("="*70)
    
    config = EvaluatorConfig(
        metric_parser={
            "regex": r"Performance: (\d+\.\d+)",
            "goal": "maximize",
            "fallback_score": 0.5
        }
    )
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("def evaluate(path): return {'combined_score': 1.0}")
        eval_file = f.name
    
    try:
        evaluator = Evaluator(config, eval_file)
        
        # Output without the expected metric
        cli_output = """
Test completed successfully
No performance metrics found
"""
        
        print("\nCLI Output (missing expected metric):")
        print(cli_output)
        
        metrics = evaluator.parse_cli_output(cli_output)
        
        print("\nResult:")
        if "error" in metrics:
            print(f"  ⚠ Metric not found - using fallback score: {metrics.get('combined_score')}")
        
    finally:
        os.unlink(eval_file)


def demo_minimize_vs_maximize():
    """Demo 5: Comparing minimize vs maximize goals"""
    print("\n" + "="*70)
    print("DEMO 5: Minimize vs Maximize Goals")
    print("="*70)
    
    # Test with minimize goal
    config_min = EvaluatorConfig(
        metric_parser={
            "regex": r"Value: (\d+)",
            "goal": "minimize"
        }
    )
    
    # Test with maximize goal
    config_max = EvaluatorConfig(
        metric_parser={
            "regex": r"Value: (\d+)",
            "goal": "maximize"
        }
    )
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("def evaluate(path): return {'combined_score': 1.0}")
        eval_file = f.name
    
    try:
        evaluator_min = Evaluator(config_min, eval_file)
        evaluator_max = Evaluator(config_max, eval_file)
        
        values = [10, 50, 100]
        
        print("\nComparing scores for different values:")
        print(f"{'Value':<10} {'Minimize Score':<20} {'Maximize Score'}")
        print("-" * 50)
        
        for val in values:
            output = f"Value: {val}"
            metrics_min = evaluator_min.parse_cli_output(output)
            metrics_max = evaluator_max.parse_cli_output(output)
            
            score_min = metrics_min.get('combined_score', 0)
            score_max = metrics_max.get('combined_score', 0)
            
            print(f"{val:<10} {score_min:<20.4f} {score_max:.4f}")
        
        print("\n✓ Lower values get higher scores with 'minimize'")
        print("✓ Higher values get higher scores with 'maximize'")
        
    finally:
        os.unlink(eval_file)


def main():
    """Run all demonstrations"""
    print("\n" + "="*70)
    print("METRIC PARSER + EVALUATOR INTEGRATION DEMO")
    print("="*70)
    print("\nThis demo shows how the metric parser integrates with the evaluator")
    print("to automatically extract performance metrics from CLI output.")
    
    demo_simple_metric_extraction()
    demo_multiple_metrics()
    demo_pytest_benchmark()
    demo_error_handling()
    demo_minimize_vs_maximize()
    
    print("\n" + "="*70)
    print("All demos completed successfully!")
    print("="*70)
    print("\nNext steps:")
    print("1. Configure metric_parser in your evaluator config")
    print("2. Use evaluator.parse_cli_output() to extract metrics")
    print("3. See docs/metric_parser_guide.md for detailed documentation")
    print()


if __name__ == "__main__":
    main()
