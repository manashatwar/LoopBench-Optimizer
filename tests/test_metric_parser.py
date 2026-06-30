"""
Tests for the Metric Parser Engine
"""

import pytest
from openevolve.metric_parser import MetricParser, MetricPattern, create_parser_from_config


class TestMetricPattern:
    """Test MetricPattern validation"""
    
    def test_valid_pattern(self):
        """Test valid pattern creation"""
        pattern = MetricPattern(
            name="latency",
            regex=r"Latency: (\d+\.\d+)ms",
            goal="minimize",
            unit="ms"
        )
        assert pattern.name == "latency"
        assert pattern.goal == "minimize"
    
    def test_invalid_goal(self):
        """Test that invalid goals are rejected"""
        with pytest.raises(ValueError, match="Invalid goal"):
            MetricPattern(
                name="latency",
                regex=r"Latency: (\d+\.\d+)ms",
                goal="reduce"  # Invalid
            )
    
    def test_invalid_regex(self):
        """Test that invalid regex patterns are rejected"""
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            MetricPattern(
                name="latency",
                regex=r"Latency: (\d+\.\d+ms",  # Missing closing paren
                goal="minimize"
            )
    
    def test_no_capture_group(self):
        """Test that patterns without capture groups are rejected"""
        with pytest.raises(ValueError, match="must contain exactly one capture group"):
            MetricPattern(
                name="latency",
                regex=r"Latency: \d+\.\d+ms",  # No capture group
                goal="minimize"
            )
    
    def test_multiple_capture_groups(self):
        """Test that patterns with multiple capture groups are rejected"""
        with pytest.raises(ValueError, match="must contain exactly one capture group"):
            MetricPattern(
                name="latency",
                regex=r"Latency: (\d+)\.(\d+)ms",  # Two capture groups
                goal="minimize"
            )


class TestMetricParser:
    """Test MetricParser functionality"""
    
    def test_simple_latency_parse(self):
        """Test parsing a simple latency metric"""
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
        
        output = "Test completed. Average latency: 12.5ms"
        metrics = parser.parse(output)
        
        assert "latency" in metrics
        assert metrics["latency"] == 12.5
        assert "latency_score" in metrics
        assert 0.0 <= metrics["latency_score"] <= 1.0
        assert "combined_score" in metrics
    
    def test_throughput_maximize(self):
        """Test parsing a throughput metric (maximize)"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="throughput",
                    regex=r"Throughput: (\d+) ops/sec",
                    goal="maximize",
                    unit="ops/sec"
                )
            ]
        )
        
        output = "Benchmark results:\nThroughput: 1500 ops/sec\nCompleted."
        metrics = parser.parse(output)
        
        assert metrics["throughput"] == 1500.0
        assert metrics["throughput_score"] > 0.5  # High throughput should score well
        assert metrics["combined_score"] == metrics["throughput_score"]
    
    def test_multiple_metrics(self):
        """Test parsing multiple metrics"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency",
                    regex=r"Latency: (\d+\.\d+)ms",
                    goal="minimize"
                ),
                MetricPattern(
                    name="throughput",
                    regex=r"Throughput: (\d+) req/s",
                    goal="maximize"
                )
            ],
            primary_metric="latency"
        )
        
        output = """
        Test Results:
        Latency: 25.3ms
        Throughput: 2000 req/s
        """
        metrics = parser.parse(output)
        
        assert metrics["latency"] == 25.3
        assert metrics["throughput"] == 2000.0
        assert "latency_score" in metrics
        assert "throughput_score" in metrics
        assert metrics["combined_score"] == metrics["latency_score"]
    
    def test_scaling_factor(self):
        """Test metric scaling"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="time",
                    regex=r"Time: (\d+)ms",
                    goal="minimize",
                    scale=0.001  # Convert ms to seconds
                )
            ]
        )
        
        output = "Time: 500ms"
        metrics = parser.parse(output)
        
        assert metrics["time"] == 0.5  # 500ms * 0.001 = 0.5s
    
    def test_metric_not_found(self):
        """Test handling of missing metrics"""
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
        
        output = "Test completed successfully (no metrics)"
        metrics = parser.parse(output)
        
        assert "error" in metrics
        assert metrics["combined_score"] == 0.0
    
    def test_empty_output(self):
        """Test handling of empty output"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency",
                    regex=r"Latency: (\d+\.\d+)ms",
                    goal="minimize"
                )
            ]
        )
        
        metrics = parser.parse("")
        assert "error" in metrics
        assert metrics["combined_score"] == 0.0
    
    def test_partial_metric_success(self):
        """Test handling when some metrics are found and others aren't"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency",
                    regex=r"Latency: (\d+\.\d+)ms",
                    goal="minimize"
                ),
                MetricPattern(
                    name="memory",
                    regex=r"Memory: (\d+)MB",
                    goal="minimize"
                )
            ]
        )
        
        output = "Latency: 15.2ms"  # Missing memory
        metrics = parser.parse(output)
        
        assert "latency" in metrics
        assert metrics["latency"] == 15.2
        assert "memory" not in metrics
        assert "combined_score" in metrics
    
    def test_case_insensitive_matching(self):
        """Test case-insensitive pattern matching"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency",
                    regex=r"average latency: (\d+\.\d+)ms",
                    goal="minimize"
                )
            ],
            ignore_case=True
        )
        
        output = "AVERAGE LATENCY: 10.5ms"
        metrics = parser.parse(output)
        
        assert metrics["latency"] == 10.5
    
    def test_multiline_matching(self):
        """Test multiline regex patterns"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="score",
                    regex=r"Score:\s+(\d+\.\d+)",  # Simplified pattern
                    goal="maximize"
                )
            ],
            multiline=True
        )
        
        output = """
        Test started
        Score: 0.95
        Test completed
        """
        metrics = parser.parse(output)
        
        assert metrics["score"] == 0.95
    
    def test_invalid_numeric_value(self):
        """Test handling of non-numeric captured values"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency",
                    regex=r"Latency: (\w+)ms",  # Captures any word
                    goal="minimize"
                )
            ]
        )
        
        output = "Latency: NANms"
        metrics = parser.parse(output)
        
        # Should fail to parse and return error
        assert "error" in metrics
    
    def test_complex_pytest_output(self):
        """Test parsing realistic pytest benchmark output"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="mean_time",
                    regex=r"Mean: (\d+\.\d+) ms",
                    goal="minimize",
                    unit="ms"
                ),
                MetricPattern(
                    name="stddev",
                    regex=r"Std Dev: (\d+\.\d+) ms",
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
        
        assert metrics["mean_time"] == 12.5
        assert metrics["stddev"] == 1.2
        assert metrics["combined_score"] == metrics["mean_time_score"]


class TestMetricParserConfig:
    """Test configuration parsing"""
    
    def test_simple_config_format(self):
        """Test simple configuration format"""
        config = {
            "regex": r"Average latency: (\d+\.\d+)ms",
            "goal": "minimize",
            "unit": "ms"
        }
        
        parser = create_parser_from_config(config)
        assert parser is not None
        assert len(parser.patterns) == 1
        assert parser.patterns[0].name == "metric"
    
    def test_advanced_config_format(self):
        """Test advanced configuration format with multiple patterns"""
        config = {
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
            "primary_metric": "latency",
            "fallback_score": 0.1
        }
        
        parser = create_parser_from_config(config)
        assert parser is not None
        assert len(parser.patterns) == 2
        assert parser.primary_metric == "latency"
        assert parser.fallback_score == 0.1
    
    def test_empty_config(self):
        """Test handling of empty config"""
        parser = create_parser_from_config(None)
        assert parser is None
        
        parser = create_parser_from_config({})
        assert parser is None
    
    def test_invalid_config_format(self):
        """Test handling of invalid config format"""
        config = {
            "invalid_key": "value"
        }
        
        parser = create_parser_from_config(config)
        assert parser is None
    
    def test_config_with_scale(self):
        """Test configuration with scaling factor"""
        config = {
            "regex": r"Time: (\d+)ms",
            "goal": "minimize",
            "scale": 0.001  # Convert ms to seconds
        }
        
        parser = create_parser_from_config(config)
        output = "Time: 1000ms"
        metrics = parser.parse(output)
        
        assert metrics["metric"] == 1.0  # 1000ms * 0.001 = 1.0s


class TestScoreComputation:
    """Test score normalization logic"""
    
    def test_minimize_score_decreases_with_value(self):
        """Test that higher values get lower scores for minimize goals"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency",
                    regex=r"(\d+)",
                    goal="minimize"
                )
            ]
        )
        
        metrics_low = parser.parse("10")
        metrics_high = parser.parse("100")
        
        assert metrics_low["latency_score"] > metrics_high["latency_score"]
    
    def test_maximize_score_increases_with_value(self):
        """Test that higher values get higher scores for maximize goals"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="throughput",
                    regex=r"(\d+)",
                    goal="maximize"
                )
            ]
        )
        
        metrics_low = parser.parse("10")
        metrics_high = parser.parse("100")
        
        assert metrics_high["throughput_score"] > metrics_low["throughput_score"]
    
    def test_zero_value_handling(self):
        """Test handling of zero values"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="metric",
                    regex=r"(\d+)",
                    goal="minimize"
                )
            ]
        )
        
        metrics = parser.parse("0")
        # Zero with minimize goal should get high score (perfect)
        assert metrics["metric_score"] > 0.9
    
    def test_negative_value_handling(self):
        """Test handling of negative values (if applicable)"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="metric",
                    regex=r"(-?\d+)",
                    goal="minimize"
                )
            ]
        )
        
        metrics = parser.parse("-10")
        # Should handle gracefully
        assert "metric" in metrics


class TestRealWorldExamples:
    """Test with real-world benchmark output formats"""
    
    def test_pytest_benchmark_format(self):
        """Test pytest-benchmark style output"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="mean",
                    regex=r"Mean:\s+(\d+\.\d+)\s+us",
                    goal="minimize",
                    unit="us"
                )
            ]
        )
        
        output = """
        ----------------------- benchmark: 1 tests -----------------------
        Name (time in us)        Mean             StdDev
        ------------------------------------------------------------------
        test_function          12.5123           0.2341
        ------------------------------------------------------------------
        """
        
        # This will fail with the current regex - need to adjust
        # Let's use a more flexible pattern
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="mean",
                    regex=r"test_function\s+(\d+\.\d+)",
                    goal="minimize",
                    unit="us"
                )
            ]
        )
        
        metrics = parser.parse(output)
        assert metrics["mean"] == 12.5123
    
    def test_custom_benchmark_format(self):
        """Test custom benchmark output"""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="execution_time",
                    regex=r"Execution time:\s+(\d+\.\d+)s",
                    goal="minimize"
                ),
                MetricPattern(
                    name="memory_usage",
                    regex=r"Peak memory:\s+(\d+)MB",
                    goal="minimize"
                )
            ],
            primary_metric="execution_time"
        )
        
        output = """
        Running benchmark...
        Execution time: 2.45s
        Peak memory: 128MB
        Benchmark complete.
        """
        
        metrics = parser.parse(output)
        assert metrics["execution_time"] == 2.45
        assert metrics["memory_usage"] == 128.0
        assert metrics["combined_score"] == metrics["execution_time_score"]
    
    def test_loopbench_fibonacci_format(self):
        """Test output matching the fibonacci optimizer example"""
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
        Execution time for fib(35): 1.234 seconds
        Result: 9227465
        Test passed!
        """
        
        metrics = parser.parse(output)
        assert metrics["execution_time"] == 1.234
        assert 0.0 <= metrics["combined_score"] <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
