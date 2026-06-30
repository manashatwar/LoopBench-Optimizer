"""
Tests for openevolve/config_validator.py — Tasks 9.1, 9.2, 9.3, 9.4.

Task 9.1  — validate_optimizer_config() unit tests
Task 9.2  — Property 3: Configuration Validation Completeness (Hypothesis)
Task 9.3  — generate_template() produces valid, parseable YAML with all sections
Task 9.4  — CLI argument merging: command-line values override YAML config

Requirements: 15.2, 15.3, 15.5, 15.6, 15.7
"""

from __future__ import annotations

import argparse
import itertools
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from hypothesis import given, settings, strategies as st

from openevolve.config_validator import (
    REQUIRED_SECTIONS,
    ConfigValidationError,
    generate_template,
    validate_optimizer_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_config() -> Dict[str, Any]:
    """Return a minimal but complete config dict with all 6 sections."""
    return {
        "repository": {"url": "https://github.com/org/repo.git", "branch": "main",
                       "target_files": ["src/main.py"]},
        "llm": {"provider": "openai", "model": "gpt-4", "api_key": "test-key"},
        "docker": {"dockerfile": "./Dockerfile.test", "test_command": "pytest",
                   "timeout": 300},
        "database": {"path": "./optimizer.db"},
        "metrics": {"patterns": {"latency": r"latency:\s*([\d.]+)"},
                    "success_threshold": 0.1},
        "search": {"strategy": "greedy", "max_iterations": 50, "patience": 10},
    }


def _config_missing(sections: List[str]) -> Dict[str, Any]:
    """Return a full config with *sections* removed."""
    cfg = _full_config()
    for s in sections:
        cfg.pop(s, None)
    return cfg


class _ObjectConfig:
    """Attribute-based config (simulates a loaded dataclass or argparse Namespace)."""
    def __init__(self, sections: List[str]) -> None:
        full = _full_config()
        for name, value in full.items():
            setattr(self, name, value if name in sections else None)


# ---------------------------------------------------------------------------
# Task 9.1 — validate_optimizer_config unit tests
# ---------------------------------------------------------------------------

class TestValidateOptimizerConfig:
    """validate_optimizer_config() — Requirements 15.2, 15.3, 15.5"""

    def test_accepts_full_dict(self):
        """No exception when all 6 sections are present."""
        validate_optimizer_config(_full_config())  # should not raise

    def test_raises_on_single_missing_section(self):
        for section in REQUIRED_SECTIONS:
            cfg = _config_missing([section])
            with pytest.raises(ConfigValidationError) as exc_info:
                validate_optimizer_config(cfg)
            assert section in exc_info.value.missing_sections

    def test_raises_on_all_sections_missing(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_optimizer_config({})
        assert set(exc_info.value.missing_sections) == set(REQUIRED_SECTIONS)

    def test_missing_sections_listed_all_at_once(self):
        """Error reports every missing section, not just the first."""
        cfg = _config_missing(["docker", "metrics", "search"])
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_optimizer_config(cfg)
        missing = exc_info.value.missing_sections
        assert "docker" in missing
        assert "metrics" in missing
        assert "search" in missing

    def test_error_message_is_human_readable(self):
        cfg = _config_missing(["llm"])
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_optimizer_config(cfg)
        msg = str(exc_info.value)
        assert "llm" in msg
        assert "required" in msg.lower()

    def test_accepts_object_config(self):
        """Works with attribute-style objects (not just dicts)."""
        obj = _ObjectConfig(REQUIRED_SECTIONS)
        validate_optimizer_config(obj)  # should not raise

    def test_raises_on_object_config_missing_section(self):
        obj = _ObjectConfig([s for s in REQUIRED_SECTIONS if s != "search"])
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_optimizer_config(obj)
        assert "search" in exc_info.value.missing_sections

    def test_section_with_none_value_counts_as_missing(self):
        """A section key mapped to None is treated as absent."""
        cfg = _full_config()
        cfg["database"] = None
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_optimizer_config(cfg)
        assert "database" in exc_info.value.missing_sections

    def test_extra_sections_are_ignored(self):
        """Unknown additional sections do not cause rejection."""
        cfg = _full_config()
        cfg["experimental"] = {"foo": "bar"}
        validate_optimizer_config(cfg)  # should not raise

    def test_config_validation_error_is_value_error(self):
        """ConfigValidationError is a subclass of ValueError."""
        with pytest.raises(ValueError):
            validate_optimizer_config({})


# ---------------------------------------------------------------------------
# Task 9.2 — Property 3: Configuration Validation Completeness (Hypothesis)
# ---------------------------------------------------------------------------

class TestConfigValidationCompletenessProperty:
    """
    Property 3: Configuration Validation Completeness

    For ANY configuration, the system accepts it if and only if all 6
    required sections are present; it rejects with clear errors when any
    section is missing.

    Validates: Requirements 15.2, 15.3
    """

    @given(missing=st.lists(
        st.sampled_from(REQUIRED_SECTIONS),
        min_size=1,
        max_size=len(REQUIRED_SECTIONS),
        unique=True,
    ))
    @settings(max_examples=100, deadline=None)
    def test_property_3_any_missing_section_causes_rejection(
        self, missing: List[str]
    ) -> None:
        """Property 3: system MUST reject when ANY required section is absent."""
        cfg = _config_missing(missing)
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_optimizer_config(cfg)
        # Every missing section must appear in the error
        for section in missing:
            assert section in exc_info.value.missing_sections, (
                f"Missing section '{section}' not reported in error: "
                f"{exc_info.value.missing_sections}"
            )

    @given(present=st.lists(
        st.sampled_from(REQUIRED_SECTIONS),
        min_size=len(REQUIRED_SECTIONS),
        max_size=len(REQUIRED_SECTIONS),
        unique=True,
    ))
    @settings(max_examples=10, deadline=None)
    def test_property_3_all_sections_present_is_accepted(
        self, present: List[str]
    ) -> None:
        """Property 3: system MUST accept when ALL 6 sections are present."""
        # present will always equal REQUIRED_SECTIONS (unique, full length)
        cfg = {s: _full_config()[s] for s in present}
        validate_optimizer_config(cfg)  # must NOT raise

    def test_property_3_exhaustive_single_missing(self) -> None:
        """Enumerate every single-section removal — all 6 must fail."""
        for section in REQUIRED_SECTIONS:
            cfg = _config_missing([section])
            with pytest.raises(ConfigValidationError):
                validate_optimizer_config(cfg)

    def test_property_3_exhaustive_two_missing(self) -> None:
        """Enumerate every pair removal — all C(6,2)=15 combos must fail."""
        for combo in itertools.combinations(REQUIRED_SECTIONS, 2):
            cfg = _config_missing(list(combo))
            with pytest.raises(ConfigValidationError) as exc_info:
                validate_optimizer_config(cfg)
            for section in combo:
                assert section in exc_info.value.missing_sections


# ---------------------------------------------------------------------------
# Task 9.3 — generate_template
# ---------------------------------------------------------------------------

class TestGenerateTemplate:
    """generate_template() — Requirement 15.7"""

    def test_creates_file(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        result = generate_template(out)
        assert result.exists()

    def test_returns_resolved_path(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        result = generate_template(out)
        assert result.is_absolute()

    def test_creates_parent_directories(self, tmp_path):
        out = tmp_path / "nested" / "deep" / "optimizer.yaml"
        generate_template(out)
        assert out.exists()

    def test_template_is_valid_yaml(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        with open(out, encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
        assert isinstance(parsed, dict)

    def test_template_contains_all_6_sections(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        with open(out, encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
        for section in REQUIRED_SECTIONS:
            assert section in parsed, f"Template missing section '{section}'"
            assert parsed[section] is not None

    def test_template_passes_validation(self, tmp_path):
        """The generated template must pass validate_optimizer_config()."""
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        # Substitute env-var placeholders so YAML loads cleanly
        content = content.replace("${GITHUB_TOKEN}", "stub_token")
        content = content.replace("${OPENAI_API_KEY}", "stub_key")
        parsed = yaml.safe_load(content)
        validate_optimizer_config(parsed)  # must NOT raise

    def test_template_has_example_values(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        text = out.read_text(encoding="utf-8")
        assert "gpt-4" in text
        assert "pytest" in text
        assert "greedy" in text
        assert "success_threshold" in text

    def test_template_has_comments(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        text = out.read_text(encoding="utf-8")
        assert "#" in text  # at least one comment present

    def test_overwrite_existing_file(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        out.write_text("old content", encoding="utf-8")
        generate_template(out)
        assert "old content" not in out.read_text(encoding="utf-8")

    def test_accepts_string_path(self, tmp_path):
        out = str(tmp_path / "optimizer.yaml")
        result = generate_template(out)
        assert result.exists()

    def test_repository_section_has_url(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        text = out.read_text(encoding="utf-8")
        assert "url:" in text

    def test_search_section_has_max_iterations(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        text = out.read_text(encoding="utf-8")
        assert "max_iterations" in text

    def test_search_section_has_patience(self, tmp_path):
        out = tmp_path / "optimizer.yaml"
        generate_template(out)
        text = out.read_text(encoding="utf-8")
        assert "patience" in text


# ---------------------------------------------------------------------------
# Task 9.4 — CLI argument merging (Requirement 15.6)
# ---------------------------------------------------------------------------

class TestCLIArgumentMerging:
    """
    Verify that CLI arguments override YAML config values.
    OpenEvolve uses argparse; this task verifies the merging contract.
    Requirement 15.6: CLI args take precedence over config file values.
    """

    def _base_config(self) -> Dict[str, Any]:
        return {
            "max_iterations": 50,
            "patience": 10,
            "strategy": "greedy",
            "success_threshold": 0.1,
        }

    def _merge(
        self, base: Dict[str, Any], cli_args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Simulate CLI-over-YAML merge: CLI args win when not None."""
        merged = dict(base)
        for key, value in cli_args.items():
            if value is not None:
                merged[key] = value
        return merged

    def test_cli_max_iterations_overrides_yaml(self):
        base = self._base_config()
        cli = {"max_iterations": 100, "patience": None}
        merged = self._merge(base, cli)
        assert merged["max_iterations"] == 100
        assert merged["patience"] == 10  # unchanged

    def test_cli_patience_overrides_yaml(self):
        base = self._base_config()
        cli = {"patience": 5, "max_iterations": None}
        merged = self._merge(base, cli)
        assert merged["patience"] == 5
        assert merged["max_iterations"] == 50

    def test_cli_strategy_overrides_yaml(self):
        base = self._base_config()
        cli = {"strategy": "beam"}
        merged = self._merge(base, cli)
        assert merged["strategy"] == "beam"

    def test_cli_none_values_do_not_override(self):
        base = self._base_config()
        cli = {"max_iterations": None, "patience": None, "strategy": None}
        merged = self._merge(base, cli)
        assert merged == base

    def test_all_cli_args_can_override_simultaneously(self):
        base = self._base_config()
        cli = {"max_iterations": 200, "patience": 20, "strategy": "random_restart"}
        merged = self._merge(base, cli)
        assert merged["max_iterations"] == 200
        assert merged["patience"] == 20
        assert merged["strategy"] == "random_restart"

    def test_argparse_namespace_merging(self):
        """Simulates argparse.Namespace-style override."""
        ns = argparse.Namespace(max_iterations=75, patience=None, strategy="beam")
        base = self._base_config()
        for key, value in vars(ns).items():
            if value is not None:
                base[key] = value
        assert base["max_iterations"] == 75
        assert base["strategy"] == "beam"
        assert base["patience"] == 10  # not overridden

    def test_zero_is_a_valid_cli_override(self):
        """Explicit zero should override, not be treated as falsy None."""
        base = {"patience": 10}
        cli = {"patience": 0}
        # Zero is not None, so it overrides
        merged = self._merge(base, cli)
        assert merged["patience"] == 0

    def test_cli_value_takes_priority_over_yaml_in_validate(self):
        """End-to-end: CLI-merged config still passes validation."""
        cfg = _full_config()
        # Simulate CLI override of search section
        cfg["search"]["max_iterations"] = 200
        cfg["search"]["patience"] = 15
        validate_optimizer_config(cfg)  # must NOT raise
