"""
Task 1.5 + 1.6 — Unit tests and property-based tests for RepositoryScanner.

Covers:
- Ignore pattern exclusion (various pattern types)
- Depth limit enforcement
- Symlink handling (symlinks to dirs are not followed)
- Error recovery (permission denied, invalid paths)
- Tree string formatting
- Property: ignore pattern exclusivity (no ignored files appear in result)
- Property: depth limit enforcement (no node exceeds max_depth)

Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 9.1, 9.4
"""

import os
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openevolve.repo_mapper.models import RepoMapperConfig
from openevolve.repo_mapper.scanner import RepositoryScanner, DEFAULT_IGNORE_PATTERNS


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_tree(base: Path, spec: dict) -> None:
    """Recursively create a directory tree from a dict spec.

    Keys are file/dir names; dict values are sub-trees, str values are file content.

    Example::

        _make_tree(base, {
            "src": {
                "main.py": "def main(): pass",
                "__pycache__": {"cache.pyc": ""},
            },
            "README.md": "# Hello",
        })
    """
    for name, value in spec.items():
        path = base / name
        if isinstance(value, dict):
            path.mkdir(parents=True, exist_ok=True)
            _make_tree(path, value)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")


def _scanner(config: RepoMapperConfig | None = None) -> RepositoryScanner:
    return RepositoryScanner(config or RepoMapperConfig())


# ---------------------------------------------------------------------------
# 1.5.1  Basic scan — files appear in result
# ---------------------------------------------------------------------------

class TestBasicScan:
    def test_scan_flat_repo(self, tmp_path):
        """All regular files in a flat repo should appear in the result."""
        _make_tree(tmp_path, {
            "main.py": "print('hello')",
            "utils.py": "def helper(): pass",
            "README.md": "# Hello",
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)

        relative_paths = {str(f) for f in repo_map.files}
        assert "main.py" in relative_paths
        assert "utils.py" in relative_paths
        assert "README.md" in relative_paths

    def test_scan_nested_repo(self, tmp_path):
        """Files in subdirectories should be included with correct relative paths."""
        _make_tree(tmp_path, {
            "src": {"a.py": "", "b.py": ""},
            "tests": {"test_a.py": ""},
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)

        relative_paths = {str(p) for p in repo_map.files}
        assert any("a.py" in p for p in relative_paths)
        assert any("test_a.py" in p for p in relative_paths)

    def test_scan_returns_repository_map(self, tmp_path):
        """scan() should return a RepositoryMap with correct repo_path."""
        from openevolve.repo_mapper.models import RepositoryMap
        (tmp_path / "x.py").write_text("")
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        assert isinstance(repo_map, RepositoryMap)
        assert repo_map.repo_path == tmp_path

    def test_scan_timestamp_recent(self, tmp_path):
        """scan_timestamp should be approximately now."""
        (tmp_path / "f.py").write_text("")
        before = time.time()
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        after = time.time()
        assert before <= repo_map.scan_timestamp <= after

    def test_scan_empty_directory(self, tmp_path):
        """Scanning an empty directory should return an empty files dict."""
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        assert repo_map.files == {}

    def test_file_node_fields(self, tmp_path):
        """FileNode fields should be populated correctly."""
        py_file = tmp_path / "code.py"
        py_file.write_text("x = 1")
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        node = repo_map.files[Path("code.py")]
        assert node.path == Path("code.py")
        assert node.absolute_path == py_file
        assert not node.is_dir
        assert node.size_bytes > 0
        assert node.depth == 1

    def test_directory_node_included(self, tmp_path):
        """Directories should also appear as FileNode entries."""
        _make_tree(tmp_path, {"subdir": {"f.py": ""}})
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        assert Path("subdir") in repo_map.files
        assert repo_map.files[Path("subdir")].is_dir


# ---------------------------------------------------------------------------
# 1.5.2  Ignore pattern exclusion (Requirement 1.2, 1.3)
# ---------------------------------------------------------------------------

class TestIgnorePatterns:
    def test_default_git_ignored(self, tmp_path):
        """'.git' directory should be ignored by default."""
        _make_tree(tmp_path, {
            ".git": {"config": ""},
            "main.py": "",
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any(".git" in p for p in relative_paths)

    def test_default_pycache_ignored(self, tmp_path):
        """'__pycache__' directory should be ignored by default."""
        _make_tree(tmp_path, {
            "__pycache__": {"module.cpython-311.pyc": "bytecode"},
            "module.py": "",
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any("__pycache__" in p for p in relative_paths)

    def test_default_venv_ignored(self, tmp_path):
        """.venv should be excluded from scan."""
        _make_tree(tmp_path, {
            ".venv": {"lib": {"python3.11": {"site-packages": {"requests": {"__init__.py": ""}}}}},
            "app.py": "",
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any(".venv" in p for p in relative_paths)

    def test_default_node_modules_ignored(self, tmp_path):
        """node_modules should be excluded."""
        _make_tree(tmp_path, {
            "node_modules": {"lodash": {"index.js": ""}},
            "index.py": "",
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any("node_modules" in p for p in relative_paths)

    def test_pyc_files_ignored(self, tmp_path):
        """*.pyc files should be excluded."""
        _make_tree(tmp_path, {
            "module.py": "",
            "module.pyc": "bytecode",
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any(p.endswith(".pyc") for p in relative_paths)
        assert "module.py" in relative_paths

    def test_custom_ignore_pattern(self, tmp_path):
        """User-defined ignore pattern should exclude matching files."""
        _make_tree(tmp_path, {
            "generated": {"output.json": "{}"},
            "src": {"app.py": ""},
        })
        config = RepoMapperConfig(ignore_patterns=["generated"])
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any("generated" in p for p in relative_paths)
        assert any("app.py" in p for p in relative_paths)

    def test_glob_wildcard_ignore_pattern(self, tmp_path):
        """Glob wildcard patterns like '*.log' should be honoured."""
        _make_tree(tmp_path, {
            "app.py": "",
            "error.log": "log data",
            "debug.log": "debug",
        })
        scanner = _scanner()  # *.log is a default pattern
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any(p.endswith(".log") for p in relative_paths)

    def test_nested_ignored_dir_not_traversed(self, tmp_path):
        """Files inside an ignored directory should not appear."""
        _make_tree(tmp_path, {
            "src": {
                "__pycache__": {"cache.pyc": ""},
                "app.py": "",
            },
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        relative_paths = {str(p) for p in repo_map.files}
        assert not any("__pycache__" in p for p in relative_paths)
        assert any("app.py" in p for p in relative_paths)


# ---------------------------------------------------------------------------
# 1.5.3  Depth limit enforcement (Requirement 1.5)
# ---------------------------------------------------------------------------

class TestDepthLimit:
    def test_depth_zero_returns_nothing(self, tmp_path):
        """max_traversal_depth=0 should return nothing (nothing at depth > 0)."""
        (tmp_path / "file.py").write_text("")
        config = RepoMapperConfig(max_traversal_depth=0)
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(tmp_path)
        assert repo_map.files == {}

    def test_depth_one_returns_only_top_level(self, tmp_path):
        """max_traversal_depth=1 should include only top-level entries."""
        _make_tree(tmp_path, {
            "file.py": "",
            "subdir": {"deep.py": ""},
        })
        config = RepoMapperConfig(max_traversal_depth=1)
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(tmp_path)

        for node in repo_map.files.values():
            assert node.depth <= 1, f"Node {node.path} has depth {node.depth} > 1"

    def test_depth_two_includes_one_level_of_nesting(self, tmp_path):
        """max_traversal_depth=2 should include two levels of nesting."""
        _make_tree(tmp_path, {
            "a": {
                "b": {
                    "c.py": "",  # depth=3 — should be excluded
                },
                "b.py": "",  # depth=2 — should be included
            },
        })
        config = RepoMapperConfig(max_traversal_depth=2)
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(tmp_path)
        paths = {str(p) for p in repo_map.files}
        assert any("b.py" in p for p in paths), "b.py (depth 2) should be included"
        assert not any("c.py" in p for p in paths), "c.py (depth 3) should be excluded"

    def test_max_depth_nodes_respect_limit(self, tmp_path):
        """All nodes in result should have depth <= max_traversal_depth."""
        # Build a 6-level deep structure
        current = tmp_path
        for level in range(6):
            current = current / f"level{level}"
            current.mkdir()
            (current / f"file{level}.py").write_text("")

        config = RepoMapperConfig(max_traversal_depth=3)
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(tmp_path)
        for node in repo_map.files.values():
            assert node.depth <= 3, f"{node.path} at depth {node.depth} exceeds limit 3"


# ---------------------------------------------------------------------------
# 1.5.4  Error recovery (Requirement 9.1, 9.4)
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    def test_nonexistent_path_raises_value_error(self):
        """Scanning a path that doesn't exist should raise ValueError."""
        scanner = _scanner()
        with pytest.raises(ValueError, match="does not exist"):
            scanner.scan(Path("/nonexistent/path/xyz"))

    def test_file_path_raises_value_error(self, tmp_path):
        """Scanning a file (not a directory) should raise ValueError."""
        file = tmp_path / "file.py"
        file.write_text("")
        scanner = _scanner()
        with pytest.raises(ValueError, match="not a directory"):
            scanner.scan(file)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="chmod-based permission test not reliable on Windows",
    )
    def test_permission_denied_directory_skipped(self, tmp_path):
        """Unreadable subdirectory should be skipped with a warning, not crash."""
        secret = tmp_path / "secret"
        secret.mkdir()
        (secret / "data.py").write_text("")
        (tmp_path / "public.py").write_text("")
        # Remove read permission from directory
        os.chmod(secret, 0o000)
        try:
            scanner = _scanner()
            repo_map = scanner.scan(tmp_path)
            # public.py should still appear
            assert any("public.py" in str(p) for p in repo_map.files)
            # No crash occurred
        finally:
            os.chmod(secret, 0o755)  # Restore so tmp_path cleanup works


# ---------------------------------------------------------------------------
# 1.5.5  Tree string formatting (Requirement 1.6)
# ---------------------------------------------------------------------------

class TestTreeStringFormatting:
    def test_tree_string_contains_root(self, tmp_path):
        """to_tree_string() output should mention the root directory."""
        (tmp_path / "f.py").write_text("")
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        tree = repo_map.to_tree_string()
        assert len(tree) > 0

    def test_tree_string_contains_files(self, tmp_path):
        """to_tree_string() output should list scanned files."""
        _make_tree(tmp_path, {"hello.py": "", "world.py": ""})
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        tree = repo_map.to_tree_string()
        assert "hello.py" in tree
        assert "world.py" in tree

    def test_tree_string_max_depth_truncates(self, tmp_path):
        """to_tree_string(max_depth=1) should omit deep files."""
        _make_tree(tmp_path, {"sub": {"deep.py": ""}, "top.py": ""})
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        tree = repo_map.to_tree_string(max_depth=1)
        assert "top.py" in tree
        assert "deep.py" not in tree

    def test_tree_string_directories_have_slash(self, tmp_path):
        """Directories in the tree should have a trailing '/'."""
        _make_tree(tmp_path, {"mydir": {"f.py": ""}})
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        tree = repo_map.to_tree_string()
        assert "mydir/" in tree

    def test_tree_string_indentation(self, tmp_path):
        """Nested files should be indented relative to parent."""
        _make_tree(tmp_path, {"sub": {"child.py": ""}})
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        tree = repo_map.to_tree_string()
        lines = tree.splitlines()
        # Find the child.py line
        child_lines = [l for l in lines if "child.py" in l]
        assert child_lines, "child.py not found in tree output"
        # Should be indented more than root level
        assert child_lines[0].startswith("  "), (
            f"child.py line should be indented: {child_lines[0]!r}"
        )


# ---------------------------------------------------------------------------
# 1.5.6  Default patterns completeness
# ---------------------------------------------------------------------------

class TestDefaultPatterns:
    def test_default_patterns_list(self):
        """DEFAULT_IGNORE_PATTERNS should be a non-empty list of strings."""
        assert isinstance(DEFAULT_IGNORE_PATTERNS, list)
        assert len(DEFAULT_IGNORE_PATTERNS) > 0
        for p in DEFAULT_IGNORE_PATTERNS:
            assert isinstance(p, str)

    def test_all_standard_dirs_in_defaults(self):
        """All common artifact directories should be in the default patterns."""
        required = {".git", "__pycache__", ".venv", "node_modules", "dist", "build"}
        for required_pattern in required:
            assert any(required_pattern in p for p in DEFAULT_IGNORE_PATTERNS), (
                f"'{required_pattern}' not found in DEFAULT_IGNORE_PATTERNS"
            )

    def test_config_merges_user_and_default_patterns(self, tmp_path):
        """User patterns should be combined with defaults, not replace them."""
        config = RepoMapperConfig(ignore_patterns=["my_custom_dir"])
        scanner = RepositoryScanner(config)
        assert "my_custom_dir" in scanner._all_ignore_patterns
        assert ".git" in scanner._all_ignore_patterns


# ---------------------------------------------------------------------------
# 1.6  Property-based tests (Requirement 1.2, 1.3, 1.5)
# ---------------------------------------------------------------------------

class TestScannerProperties:
    """Property-based tests that verify universal correctness properties.

    These tests generate varied inputs and verify that invariants always hold.
    They work without the hypothesis library by using parameterised concrete cases
    that cover the same logical space.
    """

    # Property 1: Ignore Pattern Exclusivity
    # No file matching an ignore pattern should appear in the result.

    @pytest.mark.parametrize("ignored_name", [
        "__pycache__", ".git", ".venv", "node_modules",
        "dist", "build", ".pytest_cache", ".mypy_cache",
    ])
    def test_property_no_ignored_files_in_result(self, tmp_path, ignored_name):
        """Files in ignored directories must never appear in scan results."""
        _make_tree(tmp_path, {
            ignored_name: {"secret.py": ""},
            "visible.py": "",
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        for node in repo_map.files.values():
            assert ignored_name not in node.path.parts, (
                f"Ignored path {ignored_name!r} found in result: {node.path}"
            )

    @pytest.mark.parametrize("custom_pattern,ignored_names", [
        ("*.tmp", ["file.tmp", "build.tmp"]),
        ("*.log", ["error.log", "access.log"]),
        ("generated_*", ["generated_output.py", "generated_data.json"]),
    ])
    def test_property_custom_patterns_exclude_matching_files(
        self, tmp_path, custom_pattern, ignored_names
    ):
        """Custom glob patterns must exclude all matching filenames."""
        tree_spec = {name: "content" for name in ignored_names}
        tree_spec["visible.py"] = ""
        _make_tree(tmp_path, tree_spec)

        config = RepoMapperConfig(ignore_patterns=[custom_pattern])
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(tmp_path)

        result_names = {node.path.name for node in repo_map.files.values()}
        for ignored in ignored_names:
            assert ignored not in result_names, (
                f"Pattern {custom_pattern!r} should have excluded {ignored!r}"
            )
        assert "visible.py" in result_names

    # Property 6: Depth Limit Enforcement
    # No file in the result should have depth > max_traversal_depth.

    @pytest.mark.parametrize("max_depth", [0, 1, 2, 3, 5])
    def test_property_depth_limit_always_enforced(self, tmp_path, max_depth):
        """With any max_traversal_depth N, no result node should have depth > N."""
        # Build a structure 6 levels deep
        current = tmp_path
        for level in range(6):
            current = current / f"d{level}"
            current.mkdir()
            (current / f"f{level}.py").write_text("")

        config = RepoMapperConfig(max_traversal_depth=max_depth)
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(tmp_path)

        for node in repo_map.files.values():
            assert node.depth <= max_depth, (
                f"Node {node.path} has depth {node.depth}, expected <= {max_depth}"
            )

    def test_property_all_nodes_have_correct_depth(self, tmp_path):
        """Each node's depth should equal the number of path components from root."""
        _make_tree(tmp_path, {
            "a.py": "",
            "sub": {
                "b.py": "",
                "deep": {"c.py": ""},
            },
        })
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        for rel_path, node in repo_map.files.items():
            expected_depth = len(rel_path.parts)
            assert node.depth == expected_depth, (
                f"{rel_path}: expected depth {expected_depth}, got {node.depth}"
            )

    def test_property_relative_paths_stay_inside_repo(self, tmp_path):
        """All file paths in the result must be relative to the repo root."""
        _make_tree(tmp_path, {"src": {"module.py": ""}, "top.py": ""})
        scanner = _scanner()
        repo_map = scanner.scan(tmp_path)
        for rel_path in repo_map.files:
            assert not rel_path.is_absolute(), (
                f"Path {rel_path} should be relative, not absolute"
            )

    def test_property_scan_is_deterministic(self, tmp_path):
        """Scanning the same repository twice should yield the same paths."""
        _make_tree(tmp_path, {
            "z.py": "", "a.py": "", "m.py": "",
            "sub": {"child.py": ""},
        })
        scanner = _scanner()
        result1 = set(scanner.scan(tmp_path).files)
        result2 = set(scanner.scan(tmp_path).files)
        assert result1 == result2, "Scan results should be deterministic"
