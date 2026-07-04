"""
Tasks 3.6 + 3.7 (part 2) — Unit and property-based tests for RelevanceScorer.

Covers (task 3.6 - unit tests):
- Directory proximity scoring: same dir, parent/child, sibling, top-level, unrelated
- Direct import scoring: 1-hop, 2-hop, 3-hop, unreachable
- Reverse import scoring: file imports target vs. not
- Name similarity scoring: exact, partial, unrelated
- Weighted combination formula
- Sort order (descending by total_score)
- Target file excluded from results
- algotune validation: evaluator.py scores high for initial_program.py

Covers (task 3.7 part 2 - property-based tests):
- Property 3: All scores in [0.0, 1.0]
- Property: Target always excluded from results
- Property: Results sorted descending
- Property: Consistent with formula

Requirements: 2.4, 2.5, 2.6, 2.7
"""

import time
from pathlib import Path
from typing import Dict

import pytest

from openevolve.repo_mapper.models import (
    FileDescriptor,
    FileNode,
    ImportGraph,
    ImportRelation,
    RepoMapperConfig,
    RepositoryMap,
    RelevanceScore,
)
from openevolve.repo_mapper.relevance_scorer import RelevanceScorer
from openevolve.repo_mapper.scanner import RepositoryScanner
from openevolve.repo_mapper.import_analyzer import ImportAnalyzer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tree(base: Path, spec: dict) -> None:
    for name, value in spec.items():
        path = base / name
        if isinstance(value, dict):
            path.mkdir(parents=True, exist_ok=True)
            _make_tree(path, value)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")


def _repo_map_from_dict(repo_path: Path, files: Dict[str, str]) -> RepositoryMap:
    """Build a RepositoryMap directly from a dict (no filesystem)."""
    nodes: Dict[Path, FileNode] = {}
    for rel_str in files:
        rel = Path(rel_str)
        nodes[rel] = FileNode(
            path=rel,
            absolute_path=repo_path / rel,
            is_dir=False,
            size_bytes=0,
            modified_time=time.time(),
            depth=len(rel.parts),
        )
    root = FileNode(
        path=Path("."),
        absolute_path=repo_path,
        is_dir=True,
        size_bytes=0,
        modified_time=time.time(),
        depth=0,
    )
    return RepositoryMap(
        repo_path=repo_path,
        root_node=root,
        files=nodes,
        scan_timestamp=time.time(),
    )


def _graph_with_edge(source: str, target: str) -> ImportGraph:
    """Create an ImportGraph with a single source→target edge."""
    graph = ImportGraph()
    graph.add_relation(ImportRelation(
        source_file=Path(source),
        target_module=target.replace("/", ".").replace(".py", ""),
        target_file=Path(target),
        import_type="absolute",
        line_number=1,
    ))
    return graph


def _scorer() -> RelevanceScorer:
    return RelevanceScorer(RepoMapperConfig())


def _scan(repo_path: Path) -> RepositoryMap:
    return RepositoryScanner(RepoMapperConfig()).scan(repo_path)


# ---------------------------------------------------------------------------
# 3.6.1  Directory proximity scoring
# ---------------------------------------------------------------------------

class TestDirectoryProximity:
    """Tests for _score_directory_proximity()."""

    def _prox(self, file_str: str, target_str: str) -> float:
        scorer = _scorer()
        return scorer._score_directory_proximity(Path(file_str), Path(target_str))

    def test_same_directory_scores_1(self):
        assert self._prox("pkg/a.py", "pkg/b.py") == 1.0

    def test_root_level_same_dir(self):
        assert self._prox("a.py", "b.py") == 1.0

    def test_file_inside_target_dir_scores_0_8(self):
        """File's parent is target's parent (parent/child dir relationship)."""
        # target is at src/main.py, file is inside src/sub/util.py
        # file.parent = src/sub, target.parent = src → parent is child of each other
        assert self._prox("src/sub/util.py", "src/main.py") == 0.8

    def test_sibling_directories_score_0_5(self):
        """Files in sibling subdirectories score 0.5."""
        # pkg/a/x.py vs pkg/b/y.py → same grandparent (pkg)
        assert self._prox("pkg/a/x.py", "pkg/b/y.py") == 0.5

    def test_same_top_level_different_subtree_scores_0_2(self):
        """Files sharing only the top-level directory score 0.2."""
        score = self._prox("myapp/deep/a/x.py", "myapp/other/b/y.py")
        assert score == 0.2

    def test_completely_different_tree_scores_0(self):
        # alpha/deep/sub/x.py vs beta/deep/sub/y.py:
        # - different top-level dirs (alpha vs beta) → should be 0.0, not sibling
        # Note: alpha/x.py vs beta/y.py actually ARE siblings (same grandparent = root)
        # so they score 0.5 — that is CORRECT. Use deeply-nested paths here to test 0.0.
        score = self._prox("alpha/deep/a/x.py", "beta/deep/b/y.py")
        # Different top-level: alpha vs beta → max is 0.0
        assert score == 0.0

    def test_same_file_stem_different_dir_scored_correctly(self):
        score = self._prox("tests/test_utils.py", "src/utils.py")
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# 3.6.2  Direct import scoring
# ---------------------------------------------------------------------------

class TestDirectImportScoring:
    """Tests for _score_direct_imports()."""

    def test_direct_import_scores_1(self):
        """target → file is a direct (1-hop) import → 1.0."""
        graph = _graph_with_edge("main.py", "utils.py")
        scorer = _scorer()
        score = scorer._score_direct_imports(Path("utils.py"), Path("main.py"), graph)
        assert score == 1.0

    def test_two_hop_import_scores_0_8(self):
        """target → A → file (2 hops) → 0.8."""
        graph = ImportGraph()
        graph.add_relation(ImportRelation(
            source_file=Path("main.py"),
            target_module="mid",
            target_file=Path("mid.py"),
            import_type="absolute",
            line_number=1,
        ))
        graph.add_relation(ImportRelation(
            source_file=Path("mid.py"),
            target_module="utils",
            target_file=Path("utils.py"),
            import_type="absolute",
            line_number=1,
        ))
        scorer = _scorer()
        score = scorer._score_direct_imports(Path("utils.py"), Path("main.py"), graph)
        assert score == 0.8

    def test_three_hop_import_scores_0_5(self):
        """target → A → B → file (3 hops) → 0.5."""
        graph = ImportGraph()
        edges = [
            ("main.py", "a.py"),
            ("a.py", "b.py"),
            ("b.py", "utils.py"),
        ]
        for src, tgt in edges:
            graph.add_relation(ImportRelation(
                source_file=Path(src),
                target_module=tgt.replace(".py", ""),
                target_file=Path(tgt),
                import_type="absolute",
                line_number=1,
            ))
        scorer = _scorer()
        score = scorer._score_direct_imports(Path("utils.py"), Path("main.py"), graph)
        assert score == 0.5

    def test_no_connection_scores_0(self):
        """No path between target and file → 0.0."""
        graph = ImportGraph()
        scorer = _scorer()
        score = scorer._score_direct_imports(Path("utils.py"), Path("main.py"), graph)
        assert score == 0.0

    def test_reverse_direction_not_counted_as_direct(self):
        """file → target (reverse direction) should not score as 'direct'."""
        graph = _graph_with_edge("utils.py", "main.py")
        scorer = _scorer()
        score = scorer._score_direct_imports(Path("utils.py"), Path("main.py"), graph)
        assert score == 0.0


# ---------------------------------------------------------------------------
# 3.6.3  Reverse import scoring
# ---------------------------------------------------------------------------

class TestReverseImportScoring:
    """Tests for _score_reverse_imports()."""

    def test_file_imports_target_scores_0_6(self):
        """file.py imports target.py (reverse dep) → 0.6."""
        graph = _graph_with_edge("file.py", "target.py")
        scorer = _scorer()
        score = scorer._score_reverse_imports(Path("file.py"), Path("target.py"), graph)
        assert score == 0.6

    def test_no_reverse_import_scores_0(self):
        graph = ImportGraph()
        scorer = _scorer()
        score = scorer._score_reverse_imports(Path("file.py"), Path("target.py"), graph)
        assert score == 0.0

    def test_target_imports_file_does_not_count_as_reverse(self):
        """If target imports file (forward dep), reverse score should be 0."""
        graph = _graph_with_edge("target.py", "file.py")
        scorer = _scorer()
        score = scorer._score_reverse_imports(Path("file.py"), Path("target.py"), graph)
        assert score == 0.0


# ---------------------------------------------------------------------------
# 3.6.4  Name similarity scoring
# ---------------------------------------------------------------------------

class TestNameSimilarity:
    """Tests for _score_name_similarity()."""

    def _sim(self, a: str, b: str) -> float:
        return _scorer()._score_name_similarity(Path(a), Path(b))

    def test_identical_stems_score_1(self):
        assert self._sim("pkg/utils.py", "utils.py") == 1.0

    def test_completely_different_names_score_low(self):
        score = self._sim("abcdef.py", "xyz123.py")
        assert score < 0.5

    def test_partial_similarity_between_0_and_1(self):
        score = self._sim("test_utils.py", "utils.py")
        assert 0.0 < score < 1.0

    def test_score_is_float(self):
        score = self._sim("a.py", "b.py")
        assert isinstance(score, float)

    def test_score_in_bounds(self):
        score = self._sim("evaluator.py", "initial_program.py")
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# 3.6.5  Full score_files() — combination and sort
# ---------------------------------------------------------------------------

class TestScoreFiles:
    def test_results_sorted_descending(self, tmp_path):
        """Results must be sorted by total_score descending."""
        _make_tree(tmp_path, {
            "main.py": "import utils\n",
            "utils.py": "def h(): pass\n",
            "unrelated.py": "",
        })
        repo_map = _scan(tmp_path)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()
        scores = scorer.score_files(Path("main.py"), repo_map, graph)
        totals = [s.total_score for s in scores]
        assert totals == sorted(totals, reverse=True), "Results not sorted descending"

    def test_target_excluded_from_results(self, tmp_path):
        """The target file itself must NOT appear in the results."""
        _make_tree(tmp_path, {
            "main.py": "import utils\n",
            "utils.py": "",
        })
        repo_map = _scan(tmp_path)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()
        scores = scorer.score_files(Path("main.py"), repo_map, graph)
        paths = [s.file_path for s in scores]
        assert Path("main.py") not in paths

    def test_directly_imported_file_ranks_highest(self, tmp_path):
        """A directly imported file should outrank an unrelated one."""
        _make_tree(tmp_path, {
            "main.py": "import utils\n",
            "utils.py": "def h(): pass\n",
            "other.py": "x = 42\n",
        })
        repo_map = _scan(tmp_path)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()
        scores = scorer.score_files(Path("main.py"), repo_map, graph)
        scores_by_path = {s.file_path: s for s in scores}

        assert scores_by_path[Path("utils.py")].total_score > scores_by_path[Path("other.py")].total_score

    def test_returns_relevance_score_instances(self, tmp_path):
        _make_tree(tmp_path, {"main.py": "", "other.py": ""})
        repo_map = _scan(tmp_path)
        graph = ImportGraph()
        scorer = _scorer()
        scores = scorer.score_files(Path("main.py"), repo_map, graph)
        for s in scores:
            assert isinstance(s, RelevanceScore)

    def test_same_dir_files_score_higher_than_different_dir(self, tmp_path):
        """Same-directory file should outscore a file in a different top-level dir."""
        _make_tree(tmp_path, {
            "src": {"main.py": "", "sibling.py": ""},
            "other": {"distant.py": ""},
        })
        repo_map = _scan(tmp_path)
        graph = ImportGraph()
        scorer = _scorer()
        scores = scorer.score_files(Path("src/main.py"), repo_map, graph)
        by_path = {s.file_path: s for s in scores}

        sibling_key = next(k for k in by_path if k.name == "sibling.py")
        distant_key = next(k for k in by_path if k.name == "distant.py")
        assert by_path[sibling_key].total_score > by_path[distant_key].total_score

    def test_absolute_target_path_normalised(self, tmp_path):
        """score_files() should accept absolute target paths."""
        _make_tree(tmp_path, {"main.py": "", "other.py": ""})
        repo_map = _scan(tmp_path)
        graph = ImportGraph()
        scorer = _scorer()
        # Pass absolute path
        abs_target = tmp_path / "main.py"
        scores = scorer.score_files(abs_target, repo_map, graph)
        paths = [s.file_path for s in scores]
        assert Path("main.py") not in paths

    def test_weighted_formula_applied(self, tmp_path):
        """Component scores × weights should roughly equal total_score."""
        _make_tree(tmp_path, {
            "target.py": "import dep\n",
            "dep.py": "",
        })
        repo_map = _scan(tmp_path)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()
        scores = scorer.score_files(Path("target.py"), repo_map, graph)
        dep_score = next(s for s in scores if s.file_path == Path("dep.py"))
        expected = (
            scorer.WEIGHT_DIRECT_IMPORT * dep_score.direct_import_score
            + scorer.WEIGHT_DIRECTORY_PROXIMITY * dep_score.directory_proximity_score
            + scorer.WEIGHT_REVERSE_IMPORT * dep_score.reverse_import_score
            + scorer.WEIGHT_NAME_SIMILARITY * dep_score.name_similarity_score
        )
        assert abs(dep_score.total_score - expected) < 1e-9


# ---------------------------------------------------------------------------
# 3.6.6  algotune validation
# ---------------------------------------------------------------------------

class TestAlgotuneValidation:
    """Validate against real examples/algotune examples (Req 10.2, 10.3)."""

    def test_evaluator_scores_high_for_initial_program(self):
        """evaluator.py should rank highly when initial_program.py is the target."""
        base = Path("examples/algotune/affine_transform_2d")
        if not base.exists():
            pytest.skip("algotune example not found")

        repo_map = _scan(base)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()

        target = Path("initial_program.py")
        if target not in repo_map.files:
            pytest.skip("initial_program.py not found")

        scores = scorer.score_files(target, repo_map, graph)
        paths = [s.file_path for s in scores]

        # evaluator.py should be present
        evaluator_path = next(
            (p for p in paths if p.name == "evaluator.py"), None
        )
        if evaluator_path is None:
            pytest.skip("evaluator.py not in repo_map")

        evaluator_score = next(s for s in scores if s.file_path == evaluator_path)
        # evaluator.py is in the same directory → proximity 1.0, so total ≥ 0.3
        assert evaluator_score.total_score >= 0.3, (
            f"evaluator.py total score {evaluator_score.total_score:.3f} too low"
        )

        # evaluator.py should be in the top 3
        top3_paths = [s.file_path.name for s in scores[:3]]
        assert "evaluator.py" in top3_paths, (
            f"evaluator.py not in top 3; top 3 are {top3_paths}"
        )


# ---------------------------------------------------------------------------
# 3.7 (Part 2)  Property-based tests for RelevanceScorer
# ---------------------------------------------------------------------------

class TestRelevanceScorerProperties:
    """Universal invariants for RelevanceScorer."""

    # Property 3: All scores in [0.0, 1.0]

    @pytest.mark.parametrize("repo_spec,target", [
        ({"a.py": "import b\n", "b.py": "", "c.py": ""}, "a.py"),
        ({"src/main.py": "import os\n", "src/utils.py": "", "tests/t.py": ""}, "src/main.py"),
        ({"m.py": "", "n.py": "import m\n"}, "m.py"),
    ])
    def test_property_all_scores_in_unit_interval(self, tmp_path, repo_spec, target):
        """Every RelevanceScore.total_score must be in [0.0, 1.0]."""
        _make_tree(tmp_path, repo_spec)
        repo_map = _scan(tmp_path)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()
        scores = scorer.score_files(Path(target), repo_map, graph)
        for s in scores:
            assert 0.0 <= s.total_score <= 1.0, (
                f"{s.file_path}: total_score={s.total_score:.4f} out of [0,1]"
            )
            assert 0.0 <= s.direct_import_score <= 1.0
            assert 0.0 <= s.reverse_import_score <= 1.0
            assert 0.0 <= s.directory_proximity_score <= 1.0
            assert 0.0 <= s.name_similarity_score <= 1.0

    def test_property_target_never_in_results(self, tmp_path):
        """The target must never appear in its own score list."""
        _make_tree(tmp_path, {"main.py": "", "other.py": "", "third.py": ""})
        repo_map = _scan(tmp_path)
        graph = ImportGraph()
        scorer = _scorer()
        scores = scorer.score_files(Path("main.py"), repo_map, graph)
        assert all(s.file_path != Path("main.py") for s in scores)

    def test_property_results_always_sorted_descending(self, tmp_path):
        """Results list must always be sorted from highest to lowest score."""
        _make_tree(tmp_path, {
            "main.py": "import utils\nimport helpers\n",
            "utils.py": "import os\n",
            "helpers.py": "",
            "unrelated/deep.py": "",
        })
        repo_map = _scan(tmp_path)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()
        scores = scorer.score_files(Path("main.py"), repo_map, graph)
        totals = [s.total_score for s in scores]
        assert totals == sorted(totals, reverse=True)

    def test_property_component_scores_sum_to_total(self, tmp_path):
        """Weighted sum of components must equal total_score (within floating-point)."""
        _make_tree(tmp_path, {"main.py": "import utils\n", "utils.py": ""})
        repo_map = _scan(tmp_path)
        graph = ImportAnalyzer(RepoMapperConfig()).analyze(repo_map)
        scorer = _scorer()
        scores = scorer.score_files(Path("main.py"), repo_map, graph)
        for s in scores:
            expected = (
                scorer.WEIGHT_DIRECT_IMPORT * s.direct_import_score
                + scorer.WEIGHT_DIRECTORY_PROXIMITY * s.directory_proximity_score
                + scorer.WEIGHT_REVERSE_IMPORT * s.reverse_import_score
                + scorer.WEIGHT_NAME_SIMILARITY * s.name_similarity_score
            )
            # Clamp to [0,1] like the implementation does
            expected = max(0.0, min(1.0, expected))
            assert abs(s.total_score - expected) < 1e-9, (
                f"{s.file_path}: expected {expected:.6f}, got {s.total_score:.6f}"
            )

    @pytest.mark.parametrize("n_files", [1, 5, 20])
    def test_property_result_count_equals_non_target_non_dir_files(
        self, tmp_path, n_files
    ):
        """Number of scores must equal number of non-dir, non-target files."""
        spec = {f"f{i}.py": "" for i in range(n_files)}
        spec["target.py"] = ""
        _make_tree(tmp_path, spec)
        repo_map = _scan(tmp_path)
        graph = ImportGraph()
        scorer = _scorer()
        scores = scorer.score_files(Path("target.py"), repo_map, graph)
        non_target_files = sum(
            1 for rel, node in repo_map.files.items()
            if not node.is_dir and rel != Path("target.py")
        )
        assert len(scores) == non_target_files
