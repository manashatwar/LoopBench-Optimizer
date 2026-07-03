"""Tests for sandbox dependency detection (loopbench/deps.py) and normalization."""
from pathlib import Path

from loopbench.deps import detect_python_deps, scan_imports, scan_repo_imports
from sandbox.runner import _normalize_packages, _resolve_image, SANDBOX_IMAGE


def _w(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


class TestScanImports:
    def test_third_party_only(self, tmp_path):
        f = _w(tmp_path / "m.py", "import os\nimport numpy as np\nfrom sys import argv\n")
        assert scan_imports(f) == ["numpy"]

    def test_import_alias_maps_to_pypi(self, tmp_path):
        f = _w(tmp_path / "m.py", "import cv2\nimport sklearn\n")
        pkgs = scan_imports(f)
        assert "opencv-python" in pkgs
        assert "scikit-learn" in pkgs

    def test_relative_import_ignored(self, tmp_path):
        f = _w(tmp_path / "m.py", "from . import helper\nfrom .util import x\n")
        assert scan_imports(f) == []

    def test_matplotlib_skipped(self, tmp_path):
        f = _w(tmp_path / "m.py", "import numpy\nimport matplotlib.pyplot as plt\n")
        assert scan_imports(f) == ["numpy"]


class TestScanRepoImports:
    def test_unions_across_files_and_excludes_local(self, tmp_path):
        _w(tmp_path / "a.py", "import numpy\nimport helper\n")   # helper is local
        _w(tmp_path / "helper.py", "import pandas\n")
        pkgs = scan_repo_imports(tmp_path)
        assert "numpy" in pkgs and "pandas" in pkgs
        assert "helper" not in pkgs

    def test_skips_venv_dirs(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        _w(tmp_path / ".venv" / "junk.py", "import tensorflow\n")
        _w(tmp_path / "main.py", "import numpy\n")
        pkgs = scan_repo_imports(tmp_path)
        assert "numpy" in pkgs
        assert "tensorflow" not in pkgs


class TestDetectPythonDeps:
    def test_requirements_is_authoritative(self, tmp_path):
        _w(tmp_path / "requirements.txt", "numpy==1.26.0\n# comment\nscipy\n")
        _w(tmp_path / "main.py", "import pandas\n")
        deps = detect_python_deps(tmp_path, tmp_path / "main.py")
        assert deps == ["numpy==1.26.0", "scipy"]

    def test_explicit_overrides_everything(self, tmp_path):
        _w(tmp_path / "requirements.txt", "numpy\n")
        deps = detect_python_deps(tmp_path, None, explicit=["torch", "scipy"])
        assert deps == ["torch", "scipy"]

    def test_falls_back_to_repo_scan(self, tmp_path):
        _w(tmp_path / "main.py", "import numpy\n")
        assert detect_python_deps(tmp_path, tmp_path / "main.py") == ["numpy"]


class TestNormalizeAndResolve:
    def test_normalize_from_string(self):
        assert _normalize_packages("numpy scipy numpy") == ["numpy", "scipy"]

    def test_normalize_from_list(self):
        assert _normalize_packages(["b", "a", "a"]) == ["a", "b"]

    def test_normalize_empty(self):
        assert _normalize_packages(None) == []
        assert _normalize_packages([]) == []

    def test_resolve_image_no_deps_is_base(self):
        # No packages -> base image, no Docker build attempted.
        assert _resolve_image({}, None) == SANDBOX_IMAGE
        assert _resolve_image({"pip_install": []}, None) == SANDBOX_IMAGE
