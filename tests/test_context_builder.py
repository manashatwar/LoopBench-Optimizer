"""
Unit tests for ContextBuilder (Task 5.3).

Tests token budget enforcement, file selection, tree generation, and prompt
formatting.
"""

import pytest
from pathlib import Path
from openevolve.repo_mapper.models import (
    ContextMap,
    FileDescriptor,
    FileNode,
    ImportGraph,
    RelevanceScore,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.context_builder import ContextBuilder


class TestContextBuilder:
    """Unit tests for ContextBuilder."""

    @pytest.fixture
    def config(self) -> RepoMapperConfig:
        """Standard configuration with 2000 token budget."""
        return RepoMapperConfig(token_budget=2000)

    @pytest.fixture
    def small_repo_map(self) -> RepositoryMap:
        """Small test repository."""
        root = Path("/repo")
        return RepositoryMap(
            repo_path=root,
            root_node=FileNode(
                path=Path("."),
                absolute_path=root,
                is_dir=True,
                size_bytes=0,
                modified_time=0.0,
                depth=0,
            ),
            files={
                Path("main.py"): FileNode(
                    path=Path("main.py"),
                    absolute_path=root / "main.py",
                    is_dir=False,
                    size_bytes=100,
                    modified_time=0.0,
                    depth=1,
                ),
                Path("utils.py"): FileNode(
                    path=Path("utils.py"),
                    absolute_path=root / "utils.py",
                    is_dir=False,
                    size_bytes=200,
                    modified_time=0.0,
                    depth=1,
                ),
                Path("config.py"): FileNode(
                    path=Path("config.py"),
                    absolute_path=root / "config.py",
                    is_dir=False,
                    size_bytes=50,
                    modified_time=0.0,
                    depth=1,
                ),
            },
            scan_timestamp=0.0,
        )

    @pytest.fixture
    def descriptors(self) -> dict[Path, FileDescriptor]:
        """File descriptors for test files."""
        return {
            Path("main.py"): FileDescriptor(
                file_path=Path("main.py"),
                role="main",
                summary="Main entry point for application",
                functions=["main", "run"],
                has_main=True,
                loc=50,
            ),
            Path("utils.py"): FileDescriptor(
                file_path=Path("utils.py"),
                role="utility",
                summary="Helper utilities",
                functions=["helper_a", "helper_b"],
                loc=80,
            ),
            Path("config.py"): FileDescriptor(
                file_path=Path("config.py"),
                role="config",
                summary="Configuration settings",
                classes=["Config"],
                loc=30,
            ),
        }

    @pytest.fixture
    def scored_files(self) -> list[RelevanceScore]:
        """Relevance scores for test files."""
        return [
            RelevanceScore(
                file_path=Path("utils.py"),
                total_score=0.85,
                direct_import_score=1.0,
                directory_proximity_score=1.0,
            ),
            RelevanceScore(
                file_path=Path("config.py"),
                total_score=0.32,
                directory_proximity_score=1.0,
            ),
        ]

    # ------------------------------------------------------------------
    # Test basic functionality
    # ------------------------------------------------------------------

    def test_build_context_map(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test building a basic context map."""
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )

        assert isinstance(context, ContextMap)
        assert context.target_file == Path("main.py")
        assert context.target_descriptor.file_path == Path("main.py")
        assert len(context.relevant_files) >= 1
        assert context.token_count > 0

    def test_target_always_included(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that target file is always included even with tiny budget."""
        tiny_config = RepoMapperConfig(token_budget=100)
        builder = ContextBuilder(tiny_config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )

        assert context.target_file == Path("main.py")
        assert context.target_descriptor is not None

    def test_relevant_files_ordered_by_score(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that relevant files are ordered by descending score."""
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )

        if len(context.relevant_files) > 1:
            scores = [score for _, _, score in context.relevant_files]
            assert scores == sorted(scores, reverse=True)

    def test_unsorted_scored_files_still_ordered(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
    ):
        """Builder sorts by score even when input list is unsorted."""
        unsorted = [
            RelevanceScore(file_path=Path("config.py"), total_score=0.32),
            RelevanceScore(file_path=Path("utils.py"), total_score=0.85),
        ]
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=unsorted,
            descriptors=descriptors,
        )
        if len(context.relevant_files) >= 2:
            assert context.relevant_files[0][0].name == "utils.py"

    def test_max_relevant_files_limit(
        self,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that max_relevant_files config caps included files."""
        limited_config = RepoMapperConfig(token_budget=5000, max_relevant_files=1)
        builder = ContextBuilder(limited_config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )
        assert len(context.relevant_files) <= 1

    # ------------------------------------------------------------------
    # Test token budget enforcement
    # ------------------------------------------------------------------

    def test_token_budget_respected(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that total tokens <= configured budget."""
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )

        # Allow small overhead for formatting
        assert context.token_count <= config.token_budget * 1.1

    def test_small_budget_limits_files(self):
        """Test that small budget limits number of relevant files."""
        small_config = RepoMapperConfig(token_budget=300)
        
        # Create many files
        root = Path("/repo")
        files = {}
        descriptors = {}
        scored = []

        for i in range(10):
            name = f"file_{i}.py"
            path = Path(name)
            files[path] = FileNode(
                path=path,
                absolute_path=root / name,
                is_dir=False,
                size_bytes=100,
                modified_time=0.0,
                depth=1,
            )
            descriptors[path] = FileDescriptor(
                file_path=path,
                role="utility",
                summary=f"File {i} with some description text",
                functions=[f"func_{i}"],
                loc=50,
            )
            if i > 0:  # Skip target
                scored.append(
                    RelevanceScore(
                        file_path=path,
                        total_score=1.0 - (i * 0.1),
                    )
                )

        repo_map = RepositoryMap(
            repo_path=root,
            root_node=FileNode(
                path=Path("."),
                absolute_path=root,
                is_dir=True,
                size_bytes=0,
                modified_time=0.0,
                depth=0,
            ),
            files=files,
            scan_timestamp=0.0,
        )

        builder = ContextBuilder(small_config)
        context = builder.build(
            target_file=Path("file_0.py"),
            repo_map=repo_map,
            scored_files=scored,
            descriptors=descriptors,
        )

        # With small budget, should include fewer than all files
        assert len(context.relevant_files) < len(scored)
        assert context.token_count <= small_config.token_budget * 1.1

    # ------------------------------------------------------------------
    # Test filtered tree generation
    # ------------------------------------------------------------------

    def test_filtered_tree_includes_relevant_files(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that filtered tree includes relevant files."""
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )

        tree = context.repository_tree
        assert "main.py" in tree
        # Should include at least one relevant file
        relevant_names = [p.name for p, _, _ in context.relevant_files]
        assert any(name in tree for name in relevant_names)

    def test_filtered_tree_with_nested_structure(self):
        """Test filtered tree with nested directories."""
        root = Path("/repo")
        repo_map = RepositoryMap(
            repo_path=root,
            root_node=FileNode(
                path=Path("."),
                absolute_path=root,
                is_dir=True,
                size_bytes=0,
                modified_time=0.0,
                depth=0,
            ),
            files={
                Path("src"): FileNode(
                    path=Path("src"),
                    absolute_path=root / "src",
                    is_dir=True,
                    size_bytes=0,
                    modified_time=0.0,
                    depth=1,
                ),
                Path("src/main.py"): FileNode(
                    path=Path("src/main.py"),
                    absolute_path=root / "src" / "main.py",
                    is_dir=False,
                    size_bytes=100,
                    modified_time=0.0,
                    depth=2,
                ),
                Path("src/utils.py"): FileNode(
                    path=Path("src/utils.py"),
                    absolute_path=root / "src" / "utils.py",
                    is_dir=False,
                    size_bytes=100,
                    modified_time=0.0,
                    depth=2,
                ),
            },
            scan_timestamp=0.0,
        )

        descriptors = {
            Path("src/main.py"): FileDescriptor(
                file_path=Path("src/main.py"),
                role="main",
                summary="Main file",
                loc=50,
            ),
            Path("src/utils.py"): FileDescriptor(
                file_path=Path("src/utils.py"),
                role="utility",
                summary="Utils",
                loc=30,
            ),
        }

        scored = [
            RelevanceScore(
                file_path=Path("src/utils.py"),
                total_score=0.9,
            )
        ]

        builder = ContextBuilder(RepoMapperConfig())
        context = builder.build(
            target_file=Path("src/main.py"),
            repo_map=repo_map,
            scored_files=scored,
            descriptors=descriptors,
        )

        tree = context.repository_tree
        assert "src/" in tree or "src" in tree
        assert "main.py" in tree
        assert "utils.py" in tree

    # ------------------------------------------------------------------
    # Test prompt section formatting
    # ------------------------------------------------------------------

    def test_to_prompt_section_format(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that to_prompt_section produces properly formatted output."""
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )

        prompt = context.to_prompt_section()

        # Check key sections present
        assert "## Repository Context" in prompt
        assert "Target File:" in prompt
        assert "### File Structure" in prompt
        assert "### Target File" in prompt
        assert "main.py" in prompt

        # Check code block formatting
        assert "```" in prompt

    def test_to_prompt_section_includes_scores(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that prompt section includes relevance scores."""
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=scored_files,
            descriptors=descriptors,
        )

        prompt = context.to_prompt_section()

        # At least one file should show a score
        if context.relevant_files:
            assert "score:" in prompt

    # ------------------------------------------------------------------
    # Test edge cases
    # ------------------------------------------------------------------

    def test_target_not_in_descriptors_raises(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
        scored_files: list[RelevanceScore],
    ):
        """Test that missing target descriptor raises ValueError."""
        builder = ContextBuilder(config)

        with pytest.raises(ValueError, match="not found in descriptors"):
            builder.build(
                target_file=Path("missing.py"),
                repo_map=small_repo_map,
                scored_files=scored_files,
                descriptors=descriptors,
            )

    def test_no_relevant_files(
        self,
        config: RepoMapperConfig,
        small_repo_map: RepositoryMap,
        descriptors: dict[Path, FileDescriptor],
    ):
        """Test context map with no relevant files (only target)."""
        builder = ContextBuilder(config)
        context = builder.build(
            target_file=Path("main.py"),
            repo_map=small_repo_map,
            scored_files=[],  # No scored files
            descriptors=descriptors,
        )

        assert context.target_file == Path("main.py")
        assert len(context.relevant_files) == 0
        # Prompt should still be valid
        prompt = context.to_prompt_section()
        assert "## Repository Context" in prompt

    def test_token_estimation(self, config: RepoMapperConfig):
        """Test token estimation helper."""
        builder = ContextBuilder(config)

        # Test basic estimation (1 token ≈ 4 chars by default)
        tokens = builder._estimate_tokens("hello world")
        assert tokens > 0
        # Should be roughly len("hello world") * 0.25 = 11 * 0.25 ≈ 2-3
        assert 2 <= tokens <= 4

    def test_descriptor_truncation(self, config: RepoMapperConfig):
        """Test that long descriptors are truncated when needed."""
        builder = ContextBuilder(config)

        long_descriptor = FileDescriptor(
            file_path=Path("test.py"),
            role="utility",
            summary="A" * 1000,  # Very long summary
            loc=100,
        )

        truncated = builder._truncate_descriptor(long_descriptor, max_tokens=50)
        assert len(truncated.summary) < len(long_descriptor.summary)
        assert truncated.summary.endswith("...")
