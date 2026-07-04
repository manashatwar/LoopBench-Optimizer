"""
Integration tests for Metric Parser with Evaluator
"""

import asyncio
import os
import tempfile
from pathlib import Path
import pytest

from openevolve.config import Config, EvaluatorConfig
from openevolve.evaluator import Evaluator
from openevolve.metric_parser import MetricParser, MetricPattern


class TestEvaluatorIntegration:
    """Test metric parser integration with Evaluator"""
    
    def test_evaluator_loads_metric_parser_from_config(self):
        """Test that evaluator initializes metric parser from config"""
        config = EvaluatorConfig(
            timeout=60,
            metric_parser={
                "regex": r"Time: (\d+\.\d+)s",
                "goal": "minimize",
                "unit": "seconds"
            }
        )
        
        # Create a minimal evaluator (no evaluation file needed for this test)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            # Check metric parser was initialized
            assert evaluator.metric_parser is not None
            assert isinstance(evaluator.metric_parser, MetricParser)
            assert len(evaluator.metric_parser.patterns) == 1
            assert evaluator.metric_parser.patterns[0].goal == "minimize"
        finally:
            os.unlink(eval_file)
    
    def test_evaluator_parse_cli_output_simple(self):
        """Test parsing simple CLI output through evaluator"""
        config = EvaluatorConfig(
            metric_parser={
                "regex": r"Execution time: (\d+\.\d+)s",
                "goal": "minimize"
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            # Parse output
            output = "Test started\nExecution time: 2.45s\nTest completed"
            metrics = evaluator.parse_cli_output(output)
            
            assert "metric" in metrics
            assert metrics["metric"] == 2.45
            assert "metric_score" in metrics
            assert "combined_score" in metrics
            assert 0.0 <= metrics["combined_score"] <= 1.0
        finally:
            os.unlink(eval_file)
    
    def test_evaluator_parse_cli_output_multiple_metrics(self):
        """Test parsing multiple metrics through evaluator"""
        config = EvaluatorConfig(
            metric_parser={
                "patterns": [
                    {
                        "name": "latency",
                        "regex": r"Latency: (\d+\.\d+)ms",
                        "goal": "minimize",
                        "unit": "ms"
                    },
                    {
                        "name": "throughput",
                        "regex": r"Throughput: (\d+) req/s",
                        "goal": "maximize",
                        "unit": "req/s"
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
            
            output = """
            Benchmark Results:
            Latency: 15.5ms
            Throughput: 2500 req/s
            Test passed
            """
            metrics = evaluator.parse_cli_output(output)
            
            assert metrics["latency"] == 15.5
            assert metrics["throughput"] == 2500.0
            assert "latency_score" in metrics
            assert "throughput_score" in metrics
            assert metrics["combined_score"] == metrics["latency_score"]
        finally:
            os.unlink(eval_file)
    
    def test_evaluator_no_metric_parser_configured(self):
        """Test that evaluator works without metric parser"""
        config = EvaluatorConfig(timeout=60)  # No metric_parser
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            assert evaluator.metric_parser is None
            
            # parse_cli_output should return empty dict
            metrics = evaluator.parse_cli_output("Some output")
            assert metrics == {}
        finally:
            os.unlink(eval_file)
    
    def test_evaluator_parse_cli_output_failure_handling(self):
        """Test that parsing failures are handled gracefully"""
        config = EvaluatorConfig(
            metric_parser={
                "regex": r"Score: (\d+\.\d+)",
                "goal": "maximize",
                "fallback_score": 0.5
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            # Output without the expected pattern
            output = "Test completed successfully"
            metrics = evaluator.parse_cli_output(output)
            
            # Should return error with fallback score
            assert "error" in metrics
            assert metrics["combined_score"] == 0.5
        finally:
            os.unlink(eval_file)
    
    def test_evaluator_parse_real_pytest_output(self):
        """Test parsing realistic pytest benchmark output"""
        config = EvaluatorConfig(
            metric_parser={
                "patterns": [
                    {
                        "name": "mean_time",
                        "regex": r"Mean: (\d+\.\d+) ms",
                        "goal": "minimize",
                        "unit": "ms"
                    }
                ]
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            output = """
            ============================= test session starts ==============================
            collected 5 items
            
            Benchmark report:
            Mean: 12.5 ms
            Std Dev: 1.2 ms
            
            ============================== 5 passed in 2.31s ===============================
            """
            metrics = evaluator.parse_cli_output(output)
            
            assert metrics["mean_time"] == 12.5
            assert "mean_time_score" in metrics
            assert 0.0 <= metrics["combined_score"] <= 1.0
        finally:
            os.unlink(eval_file)


class TestConfigIntegration:
    """Test metric parser integration with Config"""
    
    def test_config_loads_metric_parser_from_dict(self):
        """Test that Config.from_dict handles metric_parser"""
        config_dict = {
            "max_iterations": 10,
            "evaluator": {
                "timeout": 120,
                "metric_parser": {
                    "regex": r"Time: (\d+\.\d+)s",
                    "goal": "minimize"
                }
            }
        }
        
        config = Config.from_dict(config_dict)
        
        assert config.evaluator.metric_parser is not None
        assert "regex" in config.evaluator.metric_parser
        assert config.evaluator.metric_parser["goal"] == "minimize"
    
    def test_config_loads_metric_parser_from_yaml(self):
        """Test that Config.from_yaml handles metric_parser"""
        yaml_content = """
max_iterations: 10
evaluator:
  timeout: 120
  metric_parser:
    regex: "Latency: (\\\\d+\\\\.\\\\d+)ms"
    goal: "minimize"
    unit: "ms"
"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            config_file = f.name
        
        try:
            config = Config.from_yaml(config_file)
            
            assert config.evaluator.metric_parser is not None
            assert "regex" in config.evaluator.metric_parser
            assert config.evaluator.metric_parser["unit"] == "ms"
        finally:
            os.unlink(config_file)
    
    def test_config_with_advanced_metric_parser(self):
        """Test Config with advanced multi-metric parser"""
        config_dict = {
            "max_iterations": 10,
            "evaluator": {
                "metric_parser": {
                    "patterns": [
                        {
                            "name": "latency",
                            "regex": r"Latency: (\d+\.\d+)ms",
                            "goal": "minimize"
                        },
                        {
                            "name": "throughput",
                            "regex": r"Throughput: (\d+) ops/sec",
                            "goal": "maximize"
                        }
                    ],
                    "primary_metric": "latency",
                    "fallback_score": 0.1
                }
            }
        }
        
        config = Config.from_dict(config_dict)
        
        assert config.evaluator.metric_parser is not None
        assert "patterns" in config.evaluator.metric_parser
        assert len(config.evaluator.metric_parser["patterns"]) == 2
        assert config.evaluator.metric_parser["primary_metric"] == "latency"


class TestEndToEndScenarios:
    """End-to-end integration scenarios"""
    
    def test_fibonacci_benchmark_scenario(self):
        """Test complete fibonacci benchmark scenario"""
        # Create a simple fibonacci evaluator
        eval_code = '''
def evaluate(program_path):
    """Simulate fibonacci benchmark output"""
    # In real scenario, this would run the program and capture output
    output = """
Testing fibonacci implementation...
Execution time for fib(35): 1.234 seconds
Result: 9227465
Test passed!
"""
    return {"stdout": output, "combined_score": 0.9}
'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(eval_code)
            eval_file = f.name
        
        try:
            config = EvaluatorConfig(
                timeout=60,
                metric_parser={
                    "regex": r"Execution time for fib\(35\): (\d+\.\d+) seconds",
                    "goal": "minimize",
                    "unit": "seconds"
                }
            )
            
            evaluator = Evaluator(config, eval_file)
            
            # Simulate the output from the evaluator
            output = """
Testing fibonacci implementation...
Execution time for fib(35): 1.234 seconds
Result: 9227465
Test passed!
"""
            metrics = evaluator.parse_cli_output(output)
            
            assert "metric" in metrics
            assert metrics["metric"] == 1.234
            assert metrics["combined_score"] > 0.0
        finally:
            os.unlink(eval_file)
    
    def test_throughput_benchmark_scenario(self):
        """Test throughput optimization scenario"""
        config = EvaluatorConfig(
            metric_parser={
                "patterns": [
                    {
                        "name": "throughput",
                        "regex": r"Throughput: (\d+) ops/sec",
                        "goal": "maximize"
                    },
                    {
                        "name": "latency",
                        "regex": r"P99 latency: (\d+\.\d+)ms",
                        "goal": "minimize"
                    }
                ],
                "primary_metric": "throughput"
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            output = """
Benchmark Results:
Throughput: 5000 ops/sec
P99 latency: 12.5ms
Success rate: 99.9%
"""
            metrics = evaluator.parse_cli_output(output)
            
            assert metrics["throughput"] == 5000.0
            assert metrics["latency"] == 12.5
            # combined_score should be based on throughput (primary_metric)
            assert metrics["combined_score"] == metrics["throughput_score"]
        finally:
            os.unlink(eval_file)
    
    def test_partial_metric_extraction(self):
        """Test scenario where some metrics are missing"""
        config = EvaluatorConfig(
            metric_parser={
                "patterns": [
                    {
                        "name": "metric_a",
                        "regex": r"Metric A: (\d+\.\d+)",
                        "goal": "maximize"
                    },
                    {
                        "name": "metric_b",
                        "regex": r"Metric B: (\d+\.\d+)",
                        "goal": "minimize"
                    }
                ]
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            # Output with only metric_a
            output = "Test results: Metric A: 0.85"
            metrics = evaluator.parse_cli_output(output)
            
            # Should have metric_a but not metric_b
            assert "metric_a" in metrics
            assert metrics["metric_a"] == 0.85
            assert "metric_b" not in metrics
            # Should still have combined_score
            assert "combined_score" in metrics
        finally:
            os.unlink(eval_file)


class TestErrorHandling:
    """Test error handling in integration"""
    
    def test_invalid_metric_parser_config(self):
        """Test that invalid metric parser config is handled gracefully"""
        config = EvaluatorConfig(
            metric_parser={
                "invalid_key": "value"  # Missing required 'regex' and 'goal'
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            # Should handle gracefully - metric_parser should be None
            assert evaluator.metric_parser is None
        finally:
            os.unlink(eval_file)
    
    def test_malformed_regex_in_config(self):
        """Test that malformed regex is caught during initialization"""
        config = EvaluatorConfig(
            metric_parser={
                "regex": r"Invalid regex (\d+\.\d+",  # Missing closing paren
                "goal": "minimize"
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            # Should not crash, but metric_parser should be None
            evaluator = Evaluator(config, eval_file)
            assert evaluator.metric_parser is None
        finally:
            os.unlink(eval_file)
    
    def test_parse_cli_output_with_exception(self):
        """Test that parse_cli_output handles exceptions gracefully"""
        config = EvaluatorConfig(
            metric_parser={
                "regex": r"Value: (\d+\.\d+)",
                "goal": "minimize"
            }
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("def evaluate(path): return {'combined_score': 1.0}")
            eval_file = f.name
        
        try:
            evaluator = Evaluator(config, eval_file)
            
            # Pass None or invalid input
            metrics = evaluator.parse_cli_output(None)
            
            # Should return empty dict on error
            assert metrics == {} or "error" in metrics
        finally:
            os.unlink(eval_file)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
