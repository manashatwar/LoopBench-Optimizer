"""
Metric Parser Engine for extracting performance metrics from CLI output.

This module provides flexible parsing of benchmark output using regex patterns,
supporting both simple single-metric extraction and complex multi-metric scenarios.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MetricPattern:
    """
    Configuration for a single metric extraction pattern.
    
    Attributes:
        name: Name of the metric (e.g., "latency", "throughput")
        regex: Regular expression to extract the value (must contain one capture group)
        goal: "minimize" or "maximize"
        unit: Optional unit suffix (e.g., "ms", "ops/sec") for logging
        scale: Optional scaling factor (e.g., 0.001 to convert ms to seconds)
    """
    name: str
    regex: str
    goal: str  # "minimize" or "maximize"
    unit: Optional[str] = None
    scale: float = 1.0
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.goal not in ("minimize", "maximize"):
            raise ValueError(
                f"Invalid goal '{self.goal}' for metric '{self.name}'. "
                f"Must be 'minimize' or 'maximize'."
            )
        
        # Validate regex has exactly one capture group
        try:
            compiled = re.compile(self.regex)
            if compiled.groups != 1:
                raise ValueError(
                    f"Regex pattern for metric '{self.name}' must contain exactly one "
                    f"capture group, found {compiled.groups}. "
                    f"Pattern: {self.regex}"
                )
        except re.error as e:
            raise ValueError(
                f"Invalid regex pattern for metric '{self.name}': {e}. "
                f"Pattern: {self.regex}"
            )


class MetricParser:
    r"""
    Flexible parser for extracting performance metrics from CLI output.
    
    The parser supports:
    - Single or multiple metrics
    - Configurable regex patterns
    - Automatic score normalization (minimize vs maximize)
    - Multi-line and single-line matching
    - Optional fallback values for missing metrics
    
    Example usage:
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
        # Returns: {"latency": 12.5, "latency_score": 0.92}
    """
    
    def __init__(
        self,
        patterns: List[MetricPattern],
        primary_metric: Optional[str] = None,
        fallback_score: float = 0.0,
        multiline: bool = True,
        ignore_case: bool = False,
    ):
        """
        Initialize the metric parser.
        
        Args:
            patterns: List of MetricPattern configurations
            primary_metric: Name of the primary metric to use for combined_score
                          (defaults to the first pattern if not specified)
            fallback_score: Score to return if parsing fails (default: 0.0)
            multiline: Enable multiline regex matching (default: True)
            ignore_case: Enable case-insensitive matching (default: False)
        """
        if not patterns:
            raise ValueError("At least one metric pattern must be provided")
        
        self.patterns = patterns
        self.primary_metric = primary_metric or patterns[0].name
        self.fallback_score = fallback_score
        self.multiline = multiline
        self.ignore_case = ignore_case
        
        # Validate primary_metric exists
        pattern_names = {p.name for p in patterns}
        if self.primary_metric not in pattern_names:
            raise ValueError(
                f"primary_metric '{self.primary_metric}' not found in patterns. "
                f"Available metrics: {', '.join(pattern_names)}"
            )
        
        # Compile regex patterns
        flags = 0
        if multiline:
            flags |= re.MULTILINE
        if ignore_case:
            flags |= re.IGNORECASE
        
        self._compiled_patterns = {}
        for pattern in patterns:
            try:
                self._compiled_patterns[pattern.name] = re.compile(pattern.regex, flags)
            except re.error as e:
                raise ValueError(
                    f"Failed to compile regex for metric '{pattern.name}': {e}"
                )
        
        logger.info(
            f"Initialized MetricParser with {len(patterns)} pattern(s). "
            f"Primary metric: {self.primary_metric}"
        )
    
    def parse(self, output: str) -> Dict[str, float]:
        """
        Parse metrics from CLI output.
        
        Args:
            output: Raw stdout/stderr from benchmark execution
        
        Returns:
            Dictionary with extracted metrics and computed scores:
            - Raw metric values (e.g., "latency": 12.5)
            - Individual scores (e.g., "latency_score": 0.92)
            - combined_score: Score based on primary metric
            
        If parsing fails, returns {"error": 0.0, "combined_score": fallback_score}
        """
        if not output or not output.strip():
            logger.warning("Empty output provided to metric parser")
            return {"error": 0.0, "combined_score": self.fallback_score}
        
        metrics = {}
        scores = {}
        missing_metrics = []
        
        for pattern in self.patterns:
            regex = self._compiled_patterns[pattern.name]
            match = regex.search(output)
            
            if match:
                try:
                    raw_value = float(match.group(1))
                    scaled_value = raw_value * pattern.scale
                    
                    # Check for NaN or infinity
                    if not (raw_value ==  raw_value) or abs(raw_value) == float('inf'):
                        logger.warning(
                            f"Invalid numeric value for metric '{pattern.name}': {match.group(1)}"
                        )
                        missing_metrics.append(pattern.name)
                        continue
                    
                    # Store raw metric value
                    metrics[pattern.name] = scaled_value
                    
                    # Compute normalized score (0.0 to 1.0)
                    score = self._compute_score(scaled_value, pattern.goal)
                    scores[f"{pattern.name}_score"] = score
                    
                    unit_str = f" {pattern.unit}" if pattern.unit else ""
                    logger.debug(
                        f"Extracted metric '{pattern.name}': {raw_value}{unit_str} "
                        f"(scaled: {scaled_value}, score: {score:.4f})"
                    )
                    
                except (ValueError, IndexError) as e:
                    logger.warning(
                        f"Failed to parse value for metric '{pattern.name}': {e}. "
                        f"Match: {match.group(0)}"
                    )
                    missing_metrics.append(pattern.name)
            else:
                logger.warning(
                    f"Metric '{pattern.name}' not found in output. "
                    f"Pattern: {pattern.regex}"
                )
                missing_metrics.append(pattern.name)
        
        # If all metrics failed to parse, return error
        if len(missing_metrics) == len(self.patterns):
            logger.error(
                f"Failed to parse any metrics from output. "
                f"Missing: {', '.join(missing_metrics)}"
            )
            self._log_output_sample(output)
            return {"error": 0.0, "combined_score": self.fallback_score}
        
        # Combine metrics with scores
        result = {**metrics, **scores}
        
        # Set combined_score to the primary metric's score
        primary_score_key = f"{self.primary_metric}_score"
        if primary_score_key in scores:
            result["combined_score"] = scores[primary_score_key]
        else:
            # Primary metric missing, use fallback
            result["combined_score"] = self.fallback_score
            logger.warning(
                f"Primary metric '{self.primary_metric}' not found, "
                f"using fallback score: {self.fallback_score}"
            )
        
        # Log partial success if some metrics missing
        if missing_metrics:
            logger.warning(
                f"Parsed {len(metrics)}/{len(self.patterns)} metrics successfully. "
                f"Missing: {', '.join(missing_metrics)}"
            )
        
        return result
    
    def _compute_score(self, value: float, goal: str) -> float:
        """
        Compute normalized score from raw metric value.
        
        For minimize goals: lower values → higher scores
        For maximize goals: higher values → higher scores
        
        Uses heuristic normalization based on reasonable value ranges.
        Override this method for custom normalization logic.
        
        Args:
            value: Raw metric value
            goal: "minimize" or "maximize"
        
        Returns:
            Normalized score between 0.0 and 1.0
        """
        if value <= 0:
            return 0.0 if goal == "maximize" else 1.0
        
        if goal == "minimize":
            # For latency/time metrics: use exponential decay
            # Assumes reasonable values are 0-100 (adjust as needed)
            # score = exp(-value / reference)
            reference = 50.0  # Adjust based on your metric scale
            score = max(0.0, min(1.0, 1.0 - (value / (value + reference))))
            return score
        else:  # maximize
            # For throughput/accuracy metrics: use logistic growth
            # Assumes values are positive and unbounded
            reference = 50.0  # Adjust based on your metric scale
            score = max(0.0, min(1.0, value / (value + reference)))
            return score
    
    def _log_output_sample(self, output: str, max_lines: int = 10):
        """Log a sample of the output for debugging."""
        lines = output.strip().split('\n')
        sample_lines = lines[:max_lines]
        sample = '\n'.join(sample_lines)
        
        if len(lines) > max_lines:
            sample += f"\n... ({len(lines) - max_lines} more lines)"
        
        logger.debug(f"Output sample:\n{sample}")


def create_parser_from_config(config: Dict) -> Optional[MetricParser]:
    """
    Create a MetricParser from configuration dictionary.
    
    Supports two formats:
    
    1. Simple format (single metric):
        metric_parser:
          regex: "Average latency: (\\d+\\.\\d+)ms"
          goal: "minimize"
    
    2. Advanced format (multiple metrics):
        metric_parser:
          patterns:
            - name: "latency"
              regex: "Latency: (\\d+\\.\\d+)ms"
              goal: "minimize"
              unit: "ms"
            - name: "throughput"
              regex: "Throughput: (\\d+\\.\\d+) ops/sec"
              goal: "maximize"
              unit: "ops/sec"
          primary_metric: "latency"
    
    Args:
        config: Configuration dictionary from YAML
    
    Returns:
        MetricParser instance or None if config is empty/invalid
    """
    if not config:
        return None
    
    try:
        # Simple format: single regex + goal
        if "regex" in config and "goal" in config:
            pattern = MetricPattern(
                name=config.get("name", "metric"),
                regex=config["regex"],
                goal=config["goal"],
                unit=config.get("unit"),
                scale=config.get("scale", 1.0),
            )
            
            return MetricParser(
                patterns=[pattern],
                fallback_score=config.get("fallback_score", 0.0),
                multiline=config.get("multiline", True),
                ignore_case=config.get("ignore_case", False),
            )
        
        # Advanced format: multiple patterns
        elif "patterns" in config:
            patterns = []
            for pattern_config in config["patterns"]:
                patterns.append(
                    MetricPattern(
                        name=pattern_config["name"],
                        regex=pattern_config["regex"],
                        goal=pattern_config["goal"],
                        unit=pattern_config.get("unit"),
                        scale=pattern_config.get("scale", 1.0),
                    )
                )
            
            return MetricParser(
                patterns=patterns,
                primary_metric=config.get("primary_metric"),
                fallback_score=config.get("fallback_score", 0.0),
                multiline=config.get("multiline", True),
                ignore_case=config.get("ignore_case", False),
            )
        
        else:
            logger.warning(
                "Invalid metric_parser configuration: must contain 'regex' + 'goal' "
                "or 'patterns' list"
            )
            return None
    
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Failed to create MetricParser from config: {e}")
        return None
