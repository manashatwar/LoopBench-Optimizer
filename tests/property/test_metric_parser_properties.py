"""
Property tests for MetricParser integration.

Task 4.1 — Verify openevolve/metric_parser.py meets requirements.
Task 4.2 — Property 9: Metric Extraction Ordering Constraint.

Correctness properties validated:
  - Property 9: Metric extraction SHALL only proceed when both stdout and
    stderr output streams were successfully captured.

Validates: Requirements 5.1, 5.2, 5.3, 5.6
"""

import re
from typing import Optional

import pytest
from hypothesis import given, settings, strategies as st

from openevolve.metric_parser import MetricParser, MetricPattern, create_parser_from_config
from sandbox.runner import verify_output_streams


# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

SAFE_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=300,
)

POSITIVE_FLOAT = st.floats(min_value=0.001, max_value=1_000_000, allow_nan=False,
                            allow_infinity=False)

GOAL = st.sampled_from(("minimize", "maximize"))


def _parser_with_latency() -> MetricParser:
    """Return a MetricParser configured for latency extraction."""
    return MetricParser(
        patterns=[
            MetricPattern(
                name="latency",
                regex=r"latency:\s*([\d.]+)",
                goal="minimize",
                unit="ms",
            )
        ]
    )


def _parser_with_throughput() -> MetricParser:
    """Return a MetricParser configured for throughput extraction."""
    return MetricParser(
        patterns=[
            MetricPattern(
                name="throughput",
                regex=r"throughput:\s*([\d.]+)",
                goal="maximize",
                unit="ops/sec",
            )
        ]
    )


def _multi_parser() -> MetricParser:
    """Return a MetricParser with latency + throughput patterns."""
    return MetricParser(
        patterns=[
            MetricPattern(name="latency", regex=r"latency:\s*([\d.]+)", goal="minimize"),
            MetricPattern(name="throughput", regex=r"throughput:\s*([\d.]+)", goal="maximize"),
        ],
        primary_metric="latency",
    )


# ===========================================================================
# Task 4.1 – Verify MetricParser meets requirements
# ===========================================================================

class TestMetricParserRequirements:
    """Verification tests: MetricParser vs. Requirements 5.1-5.6."""

    # ── Requirement 5.1: extract numeric values ────────────────────────────

    @given(value=POSITIVE_FLOAT)
    @settings(max_examples=50, deadline=None)
    def test_req_5_1_extracts_numeric_value(self, value: float) -> None:
        """Req 5.1: MetricParser extracts numeric performance values from output."""
        parser = _parser_with_latency()
        output = f"Results: latency: {value} ms elapsed"
        result = parser.parse(output)
        assert "latency" in result
        assert abs(result["latency"] - value) < 1e-6

    def test_req_5_1_missing_metric_falls_back(self) -> None:
        """Req 5.1: When metric not found, parser returns fallback score."""
        parser = MetricParser(
            patterns=[MetricPattern(name="latency", regex=r"latency:\s*([\d.]+)",
                                    goal="minimize")],
            fallback_score=0.0,
        )
        result = parser.parse("no metrics here")
        assert result["combined_score"] == 0.0
        assert "error" in result

    # ── Requirement 5.2: configurable regex patterns ───────────────────────

    def test_req_5_2_custom_regex_pattern(self) -> None:
        """Req 5.2: MetricParser supports configurable regex patterns."""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="exec_time",
                    regex=r"Execution time: ([\d.]+)s",
                    goal="minimize",
                )
            ]
        )
        output = "Benchmark done. Execution time: 3.14s"
        result = parser.parse(output)
        assert "exec_time" in result
        assert abs(result["exec_time"] - 3.14) < 1e-6

    def test_req_5_2_multiple_patterns_extracted(self) -> None:
        """Req 5.2: Multiple configurable patterns all extracted."""
        parser = _multi_parser()
        output = "latency: 10.5 ms  throughput: 250.0 ops"
        result = parser.parse(output)
        assert "latency" in result
        assert "throughput" in result
        assert abs(result["latency"] - 10.5) < 1e-6
        assert abs(result["throughput"] - 250.0) < 1e-6

    # ── Requirement 5.3: combine multiple metrics into single score ─────────

    def test_req_5_3_combined_score_present(self) -> None:
        """Req 5.3: combined_score is always present in result."""
        parser = _parser_with_latency()
        result = parser.parse("latency: 42.0")
        assert "combined_score" in result

    def test_req_5_3_combined_score_tracks_primary_metric(self) -> None:
        """Req 5.3: combined_score reflects primary metric score."""
        parser = _multi_parser()
        output = "latency: 5.0  throughput: 100.0"
        result = parser.parse(output)
        assert "combined_score" in result
        assert result["combined_score"] == result["latency_score"]

    # ── Requirement 5.5: normalize metrics consistently ────────────────────

    def test_req_5_5_minimize_higher_score_for_lower_value(self) -> None:
        """Req 5.5: For minimize goal, lower value → higher score."""
        parser = _parser_with_latency()
        low = parser.parse("latency: 1.0")["latency_score"]
        high = parser.parse("latency: 1000.0")["latency_score"]
        assert low > high, "Lower latency should yield higher score"

    def test_req_5_5_maximize_higher_score_for_higher_value(self) -> None:
        """Req 5.5: For maximize goal, higher value → higher score."""
        parser = _parser_with_throughput()
        low = parser.parse("throughput: 1.0")["throughput_score"]
        high = parser.parse("throughput: 10000.0")["throughput_score"]
        assert high > low, "Higher throughput should yield higher score"

    @given(value=POSITIVE_FLOAT)
    @settings(max_examples=50, deadline=None)
    def test_req_5_5_score_bounded_zero_to_one(self, value: float) -> None:
        """Req 5.5: Scores always fall in [0.0, 1.0]."""
        for goal in ("minimize", "maximize"):
            parser = MetricParser(
                patterns=[MetricPattern(name="m", regex=r"m:\s*([\d.]+)", goal=goal)]
            )
            result = parser.parse(f"m: {value}")
            score = result.get("m_score", result.get("combined_score", 0.0))
            assert 0.0 <= score <= 1.0, (
                f"Score {score} out of bounds for goal={goal}, value={value}"
            )

    # ── Requirement 5.6: support minimize / maximize / custom ──────────────

    @given(goal=GOAL)
    @settings(max_examples=10, deadline=None)
    def test_req_5_6_both_goals_accepted(self, goal: str) -> None:
        """Req 5.6: Both minimize and maximize goals supported."""
        parser = MetricParser(
            patterns=[MetricPattern(name="m", regex=r"m:\s*([\d.]+)", goal=goal)]
        )
        result = parser.parse("m: 50.0")
        assert "m_score" in result
        assert "combined_score" in result

    def test_req_5_6_scale_factor_applied(self) -> None:
        """Req 5.6: scale factor converts units correctly."""
        # scale=0.001 converts ms → seconds
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency_s",
                    regex=r"latency_ms:\s*([\d.]+)",
                    goal="minimize",
                    scale=0.001,
                )
            ]
        )
        result = parser.parse("latency_ms: 500")
        assert abs(result["latency_s"] - 0.5) < 1e-9

    # ── create_parser_from_config factory ─────────────────────────────────

    def test_create_parser_simple_format(self) -> None:
        """create_parser_from_config works with simple regex+goal config."""
        cfg = {"regex": r"score:\s*([\d.]+)", "goal": "maximize"}
        parser = create_parser_from_config(cfg)
        assert parser is not None
        result = parser.parse("score: 0.95")
        assert "metric" in result
        assert abs(result["metric"] - 0.95) < 1e-6

    def test_create_parser_advanced_format(self) -> None:
        """create_parser_from_config works with multi-pattern config."""
        cfg = {
            "patterns": [
                {"name": "latency", "regex": r"lat:\s*([\d.]+)", "goal": "minimize"},
                {"name": "tps", "regex": r"tps:\s*([\d.]+)", "goal": "maximize"},
            ],
            "primary_metric": "latency",
        }
        parser = create_parser_from_config(cfg)
        assert parser is not None
        result = parser.parse("lat: 12.0  tps: 300.0")
        assert "latency" in result
        assert "tps" in result

    def test_create_parser_returns_none_for_empty_config(self) -> None:
        """create_parser_from_config returns None for empty config."""
        assert create_parser_from_config({}) is None
        assert create_parser_from_config(None) is None  # type: ignore[arg-type]

    def test_invalid_goal_raises(self) -> None:
        """MetricPattern raises ValueError for invalid goal."""
        with pytest.raises(ValueError, match="minimize.*maximize"):
            MetricPattern(name="m", regex=r"m:\s*([\d.]+)", goal="explode")

    def test_invalid_regex_raises(self) -> None:
        """MetricPattern raises ValueError for invalid regex."""
        with pytest.raises(ValueError):
            MetricPattern(name="m", regex=r"[invalid", goal="minimize")

    def test_regex_requires_exactly_one_group(self) -> None:
        """MetricPattern raises ValueError when regex has != 1 capture group."""
        with pytest.raises(ValueError, match="capture group"):
            MetricPattern(name="m", regex=r"no_group", goal="minimize")
        with pytest.raises(ValueError, match="capture group"):
            MetricPattern(name="m", regex=r"(a)(b)", goal="minimize")

    def test_empty_output_returns_fallback(self) -> None:
        """Empty output returns fallback score without raising."""
        parser = _parser_with_latency()
        result = parser.parse("")
        assert result["combined_score"] == parser.fallback_score

    # ── Integration: parse() from stdout vs. stderr ────────────────────────

    def test_parse_works_on_stdout_content(self) -> None:
        """MetricParser works on typical pytest/benchmark stdout."""
        stdout = (
            "test_bench PASSED\n"
            "Benchmark results:\n"
            "  latency: 23.4 ms avg\n"
            "  throughput: 420.0 ops/sec\n"
        )
        parser = _multi_parser()
        result = parser.parse(stdout)
        assert abs(result["latency"] - 23.4) < 1e-6
        assert abs(result["throughput"] - 420.0) < 1e-6

    def test_parse_works_on_stderr_content(self) -> None:
        """MetricParser also works if metric is in stderr."""
        stderr = "[WARN] latency: 99.9 high!\n"
        parser = _parser_with_latency()
        result = parser.parse(stderr)
        assert "latency" in result
        assert abs(result["latency"] - 99.9) < 1e-6


# ===========================================================================
# Task 4.2 – Property 9: Metric Extraction Ordering Constraint
# ===========================================================================

def _extract_metrics_gated(
    stdout: Optional[str],
    stderr: Optional[str],
    parser: MetricParser,
) -> Optional[dict]:
    """
    Gate metric extraction behind output-stream verification.

    This is the integration pattern the OptimizerLoop will use:
      1. Run Docker sandbox → get stdout, stderr
      2. Verify both streams captured (Property 1 / Property 2)
      3. ONLY THEN call MetricParser.parse()

    Returns None when streams are incomplete; dict of metrics otherwise.
    Validates: Requirements 5.1, Property 9.
    """
    if not verify_output_streams(stdout, stderr):
        return None  # metric extraction must NOT proceed
    combined = (stdout or "") + "\n" + (stderr or "")
    return parser.parse(combined)


class TestMetricExtractionOrderingConstraint:
    """Property 9: metric extraction proceeds iff both streams are captured."""

    # ── Both streams present → extraction runs ─────────────────────────────

    @given(value=POSITIVE_FLOAT)
    @settings(max_examples=50, deadline=None)
    def test_property_9_both_streams_allow_extraction(self, value: float) -> None:
        """Property 9: When both streams captured, extraction proceeds."""
        parser = _parser_with_latency()
        stdout = f"latency: {value}"
        stderr = "no error"

        result = _extract_metrics_gated(stdout, stderr, parser)

        assert result is not None, "Extraction must proceed when both streams present"
        assert "latency" in result
        assert abs(result["latency"] - value) < 1e-6

    # ── Missing stdout → extraction blocked ────────────────────────────────

    @given(stderr=SAFE_TEXT)
    @settings(max_examples=50, deadline=None)
    def test_property_9_missing_stdout_blocks_extraction(self, stderr: str) -> None:
        """Property 9: When stdout is None, extraction MUST NOT proceed."""
        parser = _parser_with_latency()

        result = _extract_metrics_gated(None, stderr, parser)

        assert result is None, (
            "Metric extraction must be prevented when stdout is missing"
        )

    # ── Missing stderr → extraction blocked ────────────────────────────────

    @given(stdout=SAFE_TEXT)
    @settings(max_examples=50, deadline=None)
    def test_property_9_missing_stderr_blocks_extraction(self, stdout: str) -> None:
        """Property 9: When stderr is None, extraction MUST NOT proceed."""
        parser = _parser_with_latency()

        result = _extract_metrics_gated(stdout, None, parser)

        assert result is None, (
            "Metric extraction must be prevented when stderr is missing"
        )

    # ── Both streams None → extraction blocked ─────────────────────────────

    def test_property_9_both_streams_none_blocks_extraction(self) -> None:
        """Property 9: When both streams None, extraction MUST NOT proceed."""
        parser = _parser_with_latency()
        result = _extract_metrics_gated(None, None, parser)
        assert result is None

    # ── Empty string streams are treated as captured ────────────────────────

    @given(
        stdout=st.just("") | SAFE_TEXT,
        stderr=st.just("") | SAFE_TEXT,
    )
    @settings(max_examples=30, deadline=None)
    def test_property_9_empty_string_counts_as_captured(
        self, stdout: str, stderr: str
    ) -> None:
        """Property 9: Empty string streams are captured (not None); extraction runs."""
        parser = _parser_with_latency()
        # Empty strings → verify_output_streams returns True
        assert verify_output_streams(stdout, stderr)
        result = _extract_metrics_gated(stdout, stderr, parser)
        # result is a dict (may be fallback if no match) but must not be None
        assert result is not None

    # ── Symmetry: None check is symmetric across stdout / stderr ────────────

    @given(captured=SAFE_TEXT)
    @settings(max_examples=25, deadline=None)
    def test_property_9_missing_stream_is_symmetric(self, captured: str) -> None:
        """Property 9 holds regardless of which stream is missing."""
        parser = _parser_with_latency()

        # Missing stdout
        result_no_stdout = _extract_metrics_gated(None, captured, parser)
        # Missing stderr
        result_no_stderr = _extract_metrics_gated(captured, None, parser)

        assert result_no_stdout is None
        assert result_no_stderr is None

    # ── Combined stdout+stderr content ─────────────────────────────────────

    def test_property_9_metric_in_stderr_extracted_when_both_present(self) -> None:
        """Property 9: Metric in stderr is accessible when both streams present."""
        parser = _parser_with_latency()
        stdout = "test output with no metrics"
        stderr = "latency: 77.7 ms"

        result = _extract_metrics_gated(stdout, stderr, parser)

        assert result is not None
        assert "latency" in result
        assert abs(result["latency"] - 77.7) < 1e-6
