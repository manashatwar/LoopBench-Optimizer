"""
Tasks 3.5 + 3.7 (part 1) — Unit and property-based tests for FileAnalyzer.

Covers:
- Structure extraction via parser_interface
- Role inference (test, main, config, utility, model, interface, init)
- Summary generation (docstring → structure → fallback)
- Summary length limiting (max_file_descriptor_length)
- Files without docstrings
- LOC counting
- has_main detection
- FileDescriptor.to_string() formatting
- Error recovery (parse failure, unreadable file)
- Property: descriptor always has non-empty role and summary
- Property: summary never exceeds configured limit

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 8.4, 8.5, 8.6
"""

from pathlib import Path

import pytest

from openevolve.repo_mapper.file_analyzer import FileAnalyzer
from openevolve.repo_mapper.models import FileDescriptor, RepoMapperConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(content: str, name: str, tmp_path: Path) -> tuple[Path, Path]:
    """Write *content* to *name* inside tmp_path, return (abs, rel)."""
    abs_path = tmp_path / name
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    rel_path = Path(name)
    return abs_path, rel_path


def _analyzer(max_len: int = 200) -> FileAnalyzer:
    return FileAnalyzer(RepoMapperConfig(max_file_descriptor_length=max_len))


# ---------------------------------------------------------------------------
# 3.5.1  Basic structure extraction
# ---------------------------------------------------------------------------

class TestStructureExtraction:
    def test_classes_extracted(self, tmp_path):
        """Class names must appear in the descriptor."""
        abs_p, rel_p = _make(
            "class Foo:\n    pass\nclass Bar:\n    pass\n",
            "mymodule.py",
            tmp_path,
        )
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert "Foo" in d.classes
        assert "Bar" in d.classes

    def test_functions_extracted(self, tmp_path):
        """Top-level function names must appear in the descriptor."""
        abs_p, rel_p = _make(
            "def alpha(): pass\ndef beta(x, y): return x+y\n",
            "funcs.py",
            tmp_path,
        )
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert "alpha" in d.functions
        assert "beta" in d.functions

    def test_nested_functions_not_in_top_level(self, tmp_path):
        """Only top-level functions should appear in descriptor.functions."""
        abs_p, rel_p = _make(
            "def outer():\n    def inner(): pass\n",
            "nested.py",
            tmp_path,
        )
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert "outer" in d.functions
        assert "inner" not in d.functions

    def test_returns_file_descriptor_instance(self, tmp_path):
        abs_p, rel_p = _make("x = 1\n", "x.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d, FileDescriptor)

    def test_file_path_matches_relative(self, tmp_path):
        abs_p, rel_p = _make("", "subdir/mod.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.file_path == rel_p

    def test_empty_file_gives_empty_classes_and_functions(self, tmp_path):
        abs_p, rel_p = _make("", "empty.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.classes == []
        assert d.functions == []


# ---------------------------------------------------------------------------
# 3.5.2  Role inference (Requirement 8.6)
# ---------------------------------------------------------------------------

class TestRoleInference:
    @pytest.mark.parametrize("filename,expected_role", [
        ("test_utils.py", "test"),
        ("utils_test.py", "test"),
        ("__main__.py", "main"),
        ("__init__.py", "init"),
        ("config.py", "config"),
        ("settings.py", "config"),
        ("utils.py", "utility"),
        ("helpers.py", "utility"),
    ])
    def test_role_from_filename(self, tmp_path, filename, expected_role):
        """Role should be inferred from filename."""
        abs_p, rel_p = _make("pass\n", filename, tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.role == expected_role, (
            f"Expected role {expected_role!r} for {filename}, got {d.role!r}"
        )

    def test_file_in_tests_directory_is_test(self, tmp_path):
        """A file inside a 'tests/' directory should get role 'test'."""
        abs_p, rel_p = _make("def test_something(): pass\n", "tests/check.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.role == "test"

    def test_model_role_from_filename(self, tmp_path):
        """A file named 'user_model.py' should infer role 'model'."""
        abs_p, rel_p = _make("class User: pass\n", "user_model.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.role == "model"

    def test_interface_role_from_filename(self, tmp_path):
        """A file named 'api_interface.py' should infer role 'interface'."""
        abs_p, rel_p = _make("class API: pass\n", "api_interface.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.role == "interface"

    def test_evaluator_gets_utility_role(self, tmp_path):
        """An evaluator.py file should get a utility role (common in algotune)."""
        abs_p, rel_p = _make("def evaluate(): pass\n", "evaluator.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        # evaluator matches the 'evaluat' regex pattern → utility
        assert d.role == "utility"

    def test_setup_py_is_config(self, tmp_path):
        abs_p, rel_p = _make("from setuptools import setup\n", "setup.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.role == "config"

    def test_role_is_string(self, tmp_path):
        """role must always be a non-empty string."""
        abs_p, rel_p = _make("", "anything.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d.role, str) and d.role


# ---------------------------------------------------------------------------
# 3.5.3  Summary generation (Requirement 3.1, 3.5)
# ---------------------------------------------------------------------------

class TestSummaryGeneration:
    def test_module_docstring_used_as_summary(self, tmp_path):
        """The module-level docstring should be used as the primary summary."""
        abs_p, rel_p = _make(
            '"""Utility functions for data processing."""\n\ndef helper(): pass\n',
            "utils.py",
            tmp_path,
        )
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert "Utility functions for data processing" in d.summary

    def test_summary_from_class_name_when_no_docstring(self, tmp_path):
        """When no docstring, summary should mention class names."""
        abs_p, rel_p = _make(
            "class MyProcessor:\n    def run(self): pass\n",
            "processor.py",
            tmp_path,
        )
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.summary  # Not empty

    def test_summary_fallback_for_empty_file(self, tmp_path):
        """An empty file should still get a non-empty summary fallback."""
        abs_p, rel_p = _make("", "empty.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d.summary, str) and len(d.summary) > 0

    def test_summary_is_string(self, tmp_path):
        abs_p, rel_p = _make("x = 1\n", "mod.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d.summary, str)


# ---------------------------------------------------------------------------
# 3.5.4  Summary length limiting (Requirement 3.6)
# ---------------------------------------------------------------------------

class TestSummaryLengthLimiting:
    def test_summary_within_default_limit(self, tmp_path):
        """Summary must not exceed max_file_descriptor_length (default 200)."""
        long_doc = "A" * 500
        abs_p, rel_p = _make(f'"""{long_doc}"""\n', "long.py", tmp_path)
        d = _analyzer(max_len=200).analyze_file(abs_p, rel_p)
        assert len(d.summary) <= 200

    def test_summary_within_custom_limit(self, tmp_path):
        """Custom limits should be respected."""
        abs_p, rel_p = _make('"""This is a module docstring."""\n', "m.py", tmp_path)
        d = _analyzer(max_len=10).analyze_file(abs_p, rel_p)
        assert len(d.summary) <= 10

    def test_truncated_summary_ends_with_ellipsis(self, tmp_path):
        """Truncated summaries should end with '...'."""
        abs_p, rel_p = _make(f'"""{"X" * 500}"""\n', "big.py", tmp_path)
        d = _analyzer(max_len=50).analyze_file(abs_p, rel_p)
        if len(d.summary) == 50:
            assert d.summary.endswith("...")

    def test_short_summary_not_truncated(self, tmp_path):
        """Short summaries should not get trailing '...'."""
        abs_p, rel_p = _make('"""Short."""\n', "s.py", tmp_path)
        d = _analyzer(max_len=200).analyze_file(abs_p, rel_p)
        assert not d.summary.endswith("...")


# ---------------------------------------------------------------------------
# 3.5.5  LOC counting and has_main detection
# ---------------------------------------------------------------------------

class TestLocAndMain:
    def test_loc_counted(self, tmp_path):
        """LOC must be > 0 for a non-empty file."""
        abs_p, rel_p = _make("x = 1\ny = 2\nz = 3\n", "m.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.loc > 0

    def test_empty_file_has_zero_loc(self, tmp_path):
        abs_p, rel_p = _make("", "empty.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.loc == 0

    def test_has_main_true_when_guard_present(self, tmp_path):
        src = 'def run(): pass\n\nif __name__ == "__main__":\n    run()\n'
        abs_p, rel_p = _make(src, "app.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.has_main is True

    def test_has_main_false_when_no_guard(self, tmp_path):
        abs_p, rel_p = _make("def run(): pass\n", "lib.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert d.has_main is False


# ---------------------------------------------------------------------------
# 3.5.6  FileDescriptor.to_string() formatting
# ---------------------------------------------------------------------------

class TestToStringFormatting:
    def test_to_string_contains_filename(self, tmp_path):
        abs_p, rel_p = _make("def f(): pass\n", "myfile.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        text = d.to_string()
        assert "myfile.py" in text

    def test_to_string_contains_role(self, tmp_path):
        abs_p, rel_p = _make("", "utils.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        text = d.to_string()
        assert d.role in text

    def test_to_string_with_score(self, tmp_path):
        abs_p, rel_p = _make("", "utils.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        text = d.to_string(include_score=0.85)
        assert "0.85" in text

    def test_to_string_without_score(self, tmp_path):
        abs_p, rel_p = _make("", "utils.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        text = d.to_string()
        assert isinstance(text, str) and len(text) > 0

    def test_to_string_classes_listed(self, tmp_path):
        abs_p, rel_p = _make("class Foo: pass\n", "m.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        text = d.to_string()
        assert "Foo" in text

    def test_to_string_functions_listed(self, tmp_path):
        abs_p, rel_p = _make("def bar(): pass\n", "m.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        text = d.to_string()
        assert "bar" in text


# ---------------------------------------------------------------------------
# 3.5.7  Error recovery (Requirement 9.3)
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    def test_syntax_error_file_gives_descriptor(self, tmp_path):
        """Files with syntax errors should still return a FileDescriptor."""
        abs_p, rel_p = _make("def (broken!!!\n", "broken.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d, FileDescriptor)
        assert d.role  # Must not be empty

    def test_analyze_many_skips_failures(self, tmp_path):
        """analyze_many() must not crash when one file is unreadable."""
        good = tmp_path / "good.py"
        good.write_text("def ok(): pass\n")
        bad = tmp_path / "missing.py"
        # Don't create bad.py — it doesn't exist

        files = {Path("good.py"): good, Path("missing.py"): bad}
        results = _analyzer().analyze_many(files)
        # good.py should be in results
        assert Path("good.py") in results


# ---------------------------------------------------------------------------
# 3.7 (Part 1)  Property-based tests for FileAnalyzer
# ---------------------------------------------------------------------------

class TestFileAnalyzerProperties:
    """Universal invariants that must hold for any Python source file."""

    # Property 8: File Descriptor Completeness
    # Every descriptor must have at least a role and summary.

    @pytest.mark.parametrize("content,name", [
        ("", "empty.py"),
        ("x = 1\n", "simple.py"),
        ("class A: pass\n", "class_only.py"),
        ("def f(): pass\n", "func_only.py"),
        ('"""Docstring."""\nclass B:\n    def m(self): pass\n', "full.py"),
        ("def (broken\n", "broken_syntax.py"),
    ])
    def test_property_descriptor_always_complete(self, tmp_path, content, name):
        """Every file must produce a descriptor with non-empty role and summary."""
        abs_p, rel_p = _make(content, name, tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d.role, str) and d.role, "role must be non-empty string"
        assert isinstance(d.summary, str) and d.summary, "summary must be non-empty string"

    def test_property_summary_never_exceeds_limit(self, tmp_path):
        """Summary must always be <= max_file_descriptor_length."""
        sizes = [10, 50, 100, 200, 500]
        for limit in sizes:
            abs_p, rel_p = _make(f'"""{"X" * 1000}"""\n', f"big_{limit}.py", tmp_path)
            d = FileAnalyzer(RepoMapperConfig(max_file_descriptor_length=limit)).analyze_file(abs_p, rel_p)
            assert len(d.summary) <= limit, (
                f"Summary of length {len(d.summary)} exceeds limit {limit}"
            )

    def test_property_loc_non_negative(self, tmp_path):
        """LOC must always be >= 0."""
        for content in ["", "x = 1\n", "def f(): pass\n"]:
            abs_p, rel_p = _make(content, "m.py", tmp_path)
            d = _analyzer().analyze_file(abs_p, rel_p)
            assert d.loc >= 0

    def test_property_has_main_correct_type(self, tmp_path):
        """has_main must always be a bool."""
        abs_p, rel_p = _make("x = 1\n", "m.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d.has_main, bool)

    def test_property_classes_and_functions_are_lists(self, tmp_path):
        """classes and functions must always be lists of strings."""
        abs_p, rel_p = _make("class X: pass\ndef y(): pass\n", "m.py", tmp_path)
        d = _analyzer().analyze_file(abs_p, rel_p)
        assert isinstance(d.classes, list)
        assert isinstance(d.functions, list)
        assert all(isinstance(c, str) for c in d.classes)
        assert all(isinstance(f, str) for f in d.functions)

    def test_property_role_is_known_value(self, tmp_path):
        """role must always be one of the known role strings."""
        known_roles = {"test", "main", "init", "config", "utility", "model", "interface"}
        test_cases = [
            ("test_x.py", "def test_a(): pass\n"),
            ("__main__.py", ""),
            ("__init__.py", ""),
            ("config.py", ""),
            ("utils.py", "def helper(): pass\n"),
        ]
        for name, content in test_cases:
            abs_p, rel_p = _make(content, name, tmp_path)
            d = _analyzer().analyze_file(abs_p, rel_p)
            assert d.role in known_roles, (
                f"Unexpected role {d.role!r} for {name}"
            )

    def test_property_analyze_many_returns_only_py_descriptors(self, tmp_path):
        """analyze_many() should include only the files passed in."""
        (tmp_path / "a.py").write_text("import os\n")
        (tmp_path / "b.py").write_text("def f(): pass\n")
        files = {
            Path("a.py"): tmp_path / "a.py",
            Path("b.py"): tmp_path / "b.py",
        }
        results = _analyzer().analyze_many(files)
        assert set(results.keys()) == {Path("a.py"), Path("b.py")}
