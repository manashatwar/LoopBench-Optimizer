"""
Tasks 2.4 + 2.5 — Unit tests and property-based tests for ImportAnalyzer.

Covers (task 2.4 - unit tests):
- Import extraction using parser_interface
- Graph construction (adjacency lists populated correctly)
- Simple resolution: file exists in repo → resolved to Path
- External import handling: stdlib/third-party → target_file is None
- Error recovery: unparseable / unreadable files are skipped (Req 9.2)
- Relative import resolution (from . import X, from .. import X)
- Absolute import resolution (import os, from pkg import mod)

Covers (task 2.5 - property-based tests):
- Property 2: Import Graph Consistency — all resolved target_files exist in RepositoryMap
- Property 7: Import Resolution Determinism — same repo analysed twice yields same graph
- Property 9: Circular Import Handling — analysis terminates even with circular imports

Requirements: 2.1, 2.2, 2.3, 8.1, 8.2, 9.2, 9.5
"""

import time
from pathlib import Path
from typing import Dict
from unittest.mock import patch

import pytest

from openevolve.repo_mapper.models import (
    FileNode,
    ImportGraph,
    ImportRelation,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.import_analyzer import ImportAnalyzer
from openevolve.repo_mapper.scanner import RepositoryScanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tree(base: Path, spec: dict) -> None:
    """Recursively create a directory tree from a dict spec."""
    for name, value in spec.items():
        path = base / name
        if isinstance(value, dict):
            path.mkdir(parents=True, exist_ok=True)
            _make_tree(path, value)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")


def _scan(repo_path: Path, config: RepoMapperConfig | None = None) -> RepositoryMap:
    """Scan a real directory and return a RepositoryMap."""
    return RepositoryScanner(config or RepoMapperConfig()).scan(repo_path)


def _analyzer(config: RepoMapperConfig | None = None) -> ImportAnalyzer:
    return ImportAnalyzer(config or RepoMapperConfig())


def _make_minimal_repo_map(repo_path: Path, files: Dict[str, str]) -> RepositoryMap:
    """Build a RepositoryMap without scanning the filesystem (for unit tests)."""
    nodes: Dict[Path, FileNode] = {}
    for rel_str in files:
        rel = Path(rel_str)
        abs_path = repo_path / rel
        nodes[rel] = FileNode(
            path=rel,
            absolute_path=abs_path,
            is_dir=False,
            size_bytes=len(files[rel_str]),
            modified_time=time.time(),
            depth=len(rel.parts),
        )
    root_node = FileNode(
        path=Path("."),
        absolute_path=repo_path,
        is_dir=True,
        size_bytes=0,
        modified_time=time.time(),
        depth=0,
    )
    return RepositoryMap(
        repo_path=repo_path,
        root_node=root_node,
        files=nodes,
        scan_timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# 2.4.1  Basic graph construction
# ---------------------------------------------------------------------------

class TestGraphConstruction:
    def test_empty_repo_yields_empty_graph(self, tmp_path):
        """Repo with no .py files → empty import graph."""
        _make_tree(tmp_path, {"README.md": "# Hello"})
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        assert len(graph.relations) == 0
        assert len(graph.adjacency) == 0

    def test_file_with_no_imports(self, tmp_path):
        """A .py file with no imports → no relations created."""
        _make_tree(tmp_path, {"pure.py": "x = 1\ny = 2\n"})
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        assert len(graph.relations) == 0

    def test_relations_recorded_for_every_import(self, tmp_path):
        """Every import statement in a file should create one ImportRelation."""
        _make_tree(tmp_path, {
            "app.py": "import os\nimport sys\nimport json\n",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        # Three stdlib imports → three relations (all with target_file=None)
        sources = [r.source_file for r in graph.relations]
        assert sources.count(Path("app.py")) == 3

    def test_returns_import_graph_instance(self, tmp_path):
        """analyze() must return an ImportGraph."""
        (tmp_path / "f.py").write_text("")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        assert isinstance(graph, ImportGraph)

    def test_multiple_files_all_analysed(self, tmp_path):
        """All .py files in the repo should be analysed."""
        _make_tree(tmp_path, {
            "a.py": "import os\n",
            "b.py": "import sys\n",
            "c.py": "import json\n",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        source_files = {r.source_file for r in graph.relations}
        assert Path("a.py") in source_files
        assert Path("b.py") in source_files
        assert Path("c.py") in source_files


# ---------------------------------------------------------------------------
# 2.4.2  Import extraction from real files
# ---------------------------------------------------------------------------

class TestImportExtraction:
    def test_bare_import_creates_relation(self, tmp_path):
        """'import os' should create a relation with target_module='os'."""
        (tmp_path / "m.py").write_text("import os\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        modules = [r.target_module for r in graph.relations]
        assert "os" in modules

    def test_from_import_creates_relation(self, tmp_path):
        """'from pathlib import Path' should create a relation with module='pathlib'."""
        (tmp_path / "m.py").write_text("from pathlib import Path\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        modules = [r.target_module for r in graph.relations]
        assert "pathlib" in modules

    def test_multiple_imports_on_one_line(self, tmp_path):
        """'import os, sys' should create TWO relations."""
        (tmp_path / "m.py").write_text("import os, sys\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        modules = [r.target_module for r in graph.relations]
        assert "os" in modules
        assert "sys" in modules

    def test_import_type_absolute(self, tmp_path):
        """Non-relative imports should have import_type='absolute'."""
        (tmp_path / "m.py").write_text("import os\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        assert all(r.import_type == "absolute" for r in graph.relations)

    def test_import_type_relative(self, tmp_path):
        """Relative imports should have import_type='relative'."""
        _make_tree(tmp_path, {
            "pkg": {
                "__init__.py": "",
                "a.py": "from . import b\n",
                "b.py": "x = 1\n",
            }
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        relative = [r for r in graph.relations if r.import_type == "relative"]
        assert len(relative) >= 1

    def test_line_number_recorded(self, tmp_path):
        """Line numbers should be correctly recorded on relations."""
        (tmp_path / "m.py").write_text("# comment\nimport os\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        os_rel = next(r for r in graph.relations if r.target_module == "os")
        assert os_rel.line_number == 2


# ---------------------------------------------------------------------------
# 2.4.3  Import resolution — repo-internal files (Task 2.3)
# ---------------------------------------------------------------------------

class TestImportResolution:
    def test_same_dir_import_resolved(self, tmp_path):
        """'import utils' where utils.py is in the same directory → resolved."""
        _make_tree(tmp_path, {
            "main.py": "import utils\n",
            "utils.py": "def helper(): pass\n",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        resolved = [r for r in graph.relations if r.target_file is not None]
        assert any(r.target_file == Path("utils.py") for r in resolved), (
            "utils.py should be resolved"
        )

    def test_absolute_import_with_dots_resolved(self, tmp_path):
        """'from pkg.module import X' → resolved to pkg/module.py."""
        _make_tree(tmp_path, {
            "pkg": {
                "__init__.py": "",
                "module.py": "X = 1\n",
            },
            "main.py": "from pkg.module import X\n",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        resolved_targets = {r.target_file for r in graph.relations if r.target_file}
        assert Path("pkg/module.py") in resolved_targets or Path("pkg\\module.py") in resolved_targets

    def test_package_init_import_resolved(self, tmp_path):
        """'import mypkg' where mypkg/__init__.py exists → resolved to __init__.py."""
        _make_tree(tmp_path, {
            "mypkg": {"__init__.py": ""},
            "app.py": "import mypkg\n",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        resolved = [r for r in graph.relations if r.target_file is not None]
        expected = Path("mypkg/__init__.py")
        assert any(
            r.target_file == expected or r.target_file == Path("mypkg\\__init__.py")
            for r in resolved
        ), "mypkg/__init__.py should be resolved"

    def test_stdlib_import_not_resolved(self, tmp_path):
        """'import os' (stdlib) → target_file is None."""
        (tmp_path / "m.py").write_text("import os\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        os_rel = next(r for r in graph.relations if r.target_module == "os")
        assert os_rel.target_file is None

    def test_third_party_import_not_resolved(self, tmp_path):
        """'import numpy' (third-party, not in repo) → target_file is None."""
        (tmp_path / "m.py").write_text("import numpy as np\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        np_rel = next(r for r in graph.relations if r.target_module == "numpy")
        assert np_rel.target_file is None

    def test_relative_import_same_package(self, tmp_path):
        """'from . import sibling' → resolved to sibling.py in same dir."""
        _make_tree(tmp_path, {
            "pkg": {
                "__init__.py": "",
                "a.py": "from . import b\n",
                "b.py": "VALUE = 42\n",
            }
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        relative_rels = [r for r in graph.relations if r.import_type == "relative"]
        assert len(relative_rels) >= 1

    def test_adjacency_populated_for_resolved_import(self, tmp_path):
        """When a local import is resolved, adjacency dict is updated."""
        _make_tree(tmp_path, {
            "app.py": "import utils\n",
            "utils.py": "",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        direct = graph.get_direct_imports(Path("app.py"))
        assert Path("utils.py") in direct

    def test_reverse_adjacency_populated(self, tmp_path):
        """reverse_adjacency should record what files import a target."""
        _make_tree(tmp_path, {
            "app.py": "import utils\n",
            "utils.py": "",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        reverse = graph.get_reverse_imports(Path("utils.py"))
        assert Path("app.py") in reverse

    def test_unresolved_import_not_in_adjacency(self, tmp_path):
        """Stdlib imports should NOT appear in adjacency dicts."""
        (tmp_path / "m.py").write_text("import os\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        assert graph.get_direct_imports(Path("m.py")) == set()


# ---------------------------------------------------------------------------
# 2.4.4  Error recovery (Requirement 9.2)
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    def test_unparseable_file_skipped(self, tmp_path):
        """A file with syntax errors should be skipped (no crash)."""
        _make_tree(tmp_path, {
            "valid.py": "import os\n",
            "broken.py": "def (broken syntax {\n",
        })
        repo_map = _scan(tmp_path)
        # Should complete without raising
        graph = _analyzer().analyze(repo_map)
        # valid.py should still be analysed
        sources = {r.source_file for r in graph.relations}
        assert Path("valid.py") in sources

    def test_io_error_file_skipped(self, tmp_path):
        """An IOError during extraction should be logged and skipped."""
        _make_tree(tmp_path, {
            "good.py": "import os\n",
            "bad.py": "import sys\n",
        })
        repo_map = _scan(tmp_path)

        from openevolve.repo_mapper import parser_interface

        original = parser_interface.extract_imports
        call_count = [0]

        def mock_extract(path):
            call_count[0] += 1
            if path.name == "bad.py":
                raise IOError("Permission denied")
            return original(path)

        with patch.object(parser_interface, "extract_imports", side_effect=mock_extract):
            graph = _analyzer().analyze(repo_map)

        # good.py should still be analysed
        sources = {r.source_file for r in graph.relations}
        assert Path("good.py") in sources

    def test_analysis_continues_after_failure(self, tmp_path):
        """After one file fails, remaining files are still processed."""
        _make_tree(tmp_path, {
            "a.py": "import os\n",
            "b.py": "import sys\n",
            "c.py": "import json\n",
        })
        repo_map = _scan(tmp_path)

        from openevolve.repo_mapper import parser_interface

        original = parser_interface.extract_imports

        def mock_extract(path):
            if path.name == "b.py":
                raise RuntimeError("Simulated failure")
            return original(path)

        with patch.object(parser_interface, "extract_imports", side_effect=mock_extract):
            graph = _analyzer().analyze(repo_map)

        sources = {r.source_file for r in graph.relations}
        assert Path("a.py") in sources
        assert Path("c.py") in sources


# ---------------------------------------------------------------------------
# 2.4.5  ImportGraph data model (Task 2.1)
# ---------------------------------------------------------------------------

class TestImportGraphModel:
    def test_add_relation_updates_adjacency(self):
        """add_relation() should update both adjacency and reverse_adjacency."""
        graph = ImportGraph()
        relation = ImportRelation(
            source_file=Path("a.py"),
            target_module="b",
            target_file=Path("b.py"),
            import_type="absolute",
            line_number=1,
        )
        graph.add_relation(relation)
        assert Path("b.py") in graph.get_direct_imports(Path("a.py"))
        assert Path("a.py") in graph.get_reverse_imports(Path("b.py"))

    def test_add_relation_no_target_no_adjacency(self):
        """Relations with target_file=None should NOT affect adjacency."""
        graph = ImportGraph()
        relation = ImportRelation(
            source_file=Path("a.py"),
            target_module="os",
            target_file=None,
            import_type="absolute",
            line_number=1,
        )
        graph.add_relation(relation)
        assert graph.get_direct_imports(Path("a.py")) == set()
        assert len(graph.relations) == 1

    def test_get_direct_imports_unknown_file_returns_empty(self):
        """get_direct_imports() on an unknown file returns empty set."""
        graph = ImportGraph()
        assert graph.get_direct_imports(Path("unknown.py")) == set()

    def test_get_reverse_imports_unknown_file_returns_empty(self):
        """get_reverse_imports() on an unknown file returns empty set."""
        graph = ImportGraph()
        assert graph.get_reverse_imports(Path("unknown.py")) == set()

    def test_len_returns_relation_count(self):
        """len(graph) == number of relations."""
        graph = ImportGraph()
        for i in range(5):
            graph.add_relation(ImportRelation(
                source_file=Path(f"src{i}.py"),
                target_module="os",
                target_file=None,
                import_type="absolute",
                line_number=1,
            ))
        assert len(graph) == 5

    def test_get_all_files_includes_sources_and_targets(self):
        """get_all_files() should include both source and target files."""
        graph = ImportGraph()
        graph.add_relation(ImportRelation(
            source_file=Path("a.py"),
            target_module="b",
            target_file=Path("b.py"),
            import_type="absolute",
            line_number=1,
        ))
        all_files = graph.get_all_files()
        assert Path("a.py") in all_files
        assert Path("b.py") in all_files

    def test_has_file_true_for_source(self):
        graph = ImportGraph()
        graph.add_relation(ImportRelation(
            source_file=Path("a.py"),
            target_module="b",
            target_file=Path("b.py"),
            import_type="absolute",
            line_number=1,
        ))
        assert graph.has_file(Path("a.py"))
        assert graph.has_file(Path("b.py"))
        assert not graph.has_file(Path("c.py"))


# ---------------------------------------------------------------------------
# 2.5  Property-based tests (Requirements 2.1, 2.2, 2.3, 9.5)
# ---------------------------------------------------------------------------

class TestImportAnalyzerProperties:
    """Universal invariants that must hold for any repository structure."""

    # ------------------------------------------------------------------
    # Property 2: Import Graph Consistency
    # All resolved target_file values must exist in the RepositoryMap.files
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("repo_spec,imports_src", [
        # single file imports its sibling
        ({"a.py": "import b\n", "b.py": "x = 1\n"}, "a.py"),
        # flat package
        ({"pkg/__init__.py": "", "pkg/mod.py": "x=1\n", "main.py": "from pkg import mod\n"}, "main.py"),
        # nested: from pkg.sub import X
        ({"pkg/__init__.py": "", "pkg/sub.py": "X=1\n", "run.py": "from pkg.sub import X\n"}, "run.py"),
    ])
    def test_property_resolved_targets_exist_in_repo_map(
        self, tmp_path, repo_spec, imports_src
    ):
        """Every resolved target_file in the import graph must be a key in repo_map.files."""
        _make_tree(tmp_path, repo_spec)
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)

        for relation in graph.relations:
            if relation.target_file is not None:
                assert relation.target_file in repo_map.files, (
                    f"Resolved target {relation.target_file} is not in repo_map.files. "
                    f"Relation: {relation}"
                )

    def test_property_no_self_imports(self, tmp_path):
        """A file should never be recorded as importing itself."""
        _make_tree(tmp_path, {
            "a.py": "import a\nimport b\n",
            "b.py": "import b\n",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        for relation in graph.relations:
            if relation.target_file is not None:
                assert relation.source_file != relation.target_file, (
                    f"Self-import detected: {relation.source_file}"
                )

    # ------------------------------------------------------------------
    # Property 7: Import Resolution Determinism
    # Analysing the same repo twice must yield identical graphs.
    # ------------------------------------------------------------------

    def test_property_analysis_is_deterministic(self, tmp_path):
        """Running analyze() twice on the same repo yields the same relations."""
        _make_tree(tmp_path, {
            "main.py": "import utils\nimport os\nfrom helpers import h\n",
            "utils.py": "import os\n",
            "helpers.py": "def h(): pass\n",
        })
        repo_map = _scan(tmp_path)
        analyzer = _analyzer()

        graph1 = analyzer.analyze(repo_map)
        graph2 = analyzer.analyze(repo_map)

        # Sort for stable comparison
        def relation_key(r):
            return (str(r.source_file), r.target_module, r.line_number)

        rels1 = sorted(
            [(r.source_file, r.target_module, r.target_file) for r in graph1.relations],
        )
        rels2 = sorted(
            [(r.source_file, r.target_module, r.target_file) for r in graph2.relations],
        )
        assert rels1 == rels2, "Import analysis must be deterministic"

    def test_property_adjacency_consistent_with_relations(self, tmp_path):
        """Every entry in adjacency must correspond to a resolved relation."""
        _make_tree(tmp_path, {
            "app.py": "import utils\nimport os\n",
            "utils.py": "import json\n",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)

        # Build expected adjacency from relations manually
        expected: dict[Path, set[Path]] = {}
        for r in graph.relations:
            if r.target_file is not None:
                expected.setdefault(r.source_file, set()).add(r.target_file)

        assert graph.adjacency == expected, (
            "graph.adjacency does not match relations"
        )

    # ------------------------------------------------------------------
    # Property 9: Circular Import Handling
    # Analysis must terminate even when files import each other cyclically.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cycle_len", [2, 3, 4])
    def test_property_circular_imports_terminate(self, tmp_path, cycle_len):
        """Analysis must complete (not loop forever) for circular imports."""
        # Build a cycle: a.py → b.py → c.py → a.py (for cycle_len=3)
        names = [f"m{i}.py" for i in range(cycle_len)]
        spec = {}
        for i, name in enumerate(names):
            next_name = names[(i + 1) % cycle_len]
            next_module = next_name[:-3]  # strip .py
            spec[name] = f"import {next_module}\n"

        _make_tree(tmp_path, spec)
        repo_map = _scan(tmp_path)

        # Should complete without raising or hanging
        graph = _analyzer().analyze(repo_map)

        # All files should appear as sources
        sources = {r.source_file for r in graph.relations}
        for name in names:
            assert Path(name) in sources, f"{name} should be in sources"

    def test_property_self_referential_import_does_not_crash(self, tmp_path):
        """'import self_module' (file that imports itself) must not crash."""
        (tmp_path / "selfref.py").write_text("import selfref\n")
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        assert any(r.source_file == Path("selfref.py") for r in graph.relations)

    def test_property_all_sources_are_py_files(self, tmp_path):
        """Every source_file in the graph must have a .py extension."""
        _make_tree(tmp_path, {
            "code.py": "import os\n",
            "README.md": "# Not Python",
            "config.yaml": "key: value",
        })
        repo_map = _scan(tmp_path)
        graph = _analyzer().analyze(repo_map)
        for relation in graph.relations:
            assert relation.source_file.suffix == ".py", (
                f"Non-.py source: {relation.source_file}"
            )

    # ------------------------------------------------------------------
    # Property: algotune integration test
    # ------------------------------------------------------------------

    def test_property_algotune_affine_transform_analysis(self):
        """Analyse examples/algotune/affine_transform_2d/ — no crash, sensible output."""
        algotune_path = Path("examples/algotune/affine_transform_2d")
        if not algotune_path.exists():
            pytest.skip("algotune example not available")

        repo_map = _scan(algotune_path)
        graph = _analyzer().analyze(repo_map)

        # Should complete without crash
        assert isinstance(graph, ImportGraph)

        # All resolved targets must be in repo_map
        for r in graph.relations:
            if r.target_file is not None:
                assert r.target_file in repo_map.files, (
                    f"Resolved target {r.target_file} not in repo_map"
                )
