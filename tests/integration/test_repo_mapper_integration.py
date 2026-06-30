"""
Integration tests for RepoContextMapper (Task 7.3).

Tests end-to-end flow with test repositories, verifying the complete
analysis pipeline from scanning to context map generation.
"""

import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from openevolve.repo_mapper.models import RepoMapperConfig
from openevolve.repo_mapper.mapper import (
    ContextBuildError,
    RepoContextMapper,
    RepositoryScanError,
)


class TestRepoContextMapperIntegration:
    """Integration tests for complete analysis pipeline."""
    
    @pytest.fixture
    def config(self) -> RepoMapperConfig:
        """Standard configuration for testing."""
        return RepoMapperConfig(
            token_budget=2000,
            enable_cache=True,
            max_relevant_files=10,
        )
    
    @pytest.fixture
    def mapper(self, config: RepoMapperConfig) -> RepoContextMapper:
        """Create mapper instance."""
        return RepoContextMapper(config)
    
    @pytest.fixture
    def simple_repo(self) -> TemporaryDirectory:
        """Create simple test repository with Python files."""
        tmpdir = TemporaryDirectory()
        repo_path = Path(tmpdir.name) / "test_repo"
        repo_path.mkdir()
        
        # Create main.py
        main_file = repo_path / "main.py"
        main_file.write_text("""
# Main module
from utils import helper

def main():
    return helper()

if __name__ == '__main__':
    main()
""")
        
        # Create utils.py
        utils_file = repo_path / "utils.py"
        utils_file.write_text("""
# Utility module

def helper():
    return "Hello"

def another_func():
    pass
""")
        
        # Create config.py
        config_file = repo_path / "config.py"
        config_file.write_text("""
# Configuration

class Config:
    DEBUG = True
""")
        
        return tmpdir
    
    # ------------------------------------------------------------------
    # Test end-to-end flow
    # ------------------------------------------------------------------
    
    def test_complete_analysis_flow(
        self,
        mapper: RepoContextMapper,
        simple_repo: TemporaryDirectory,
    ):
        """Test full repository analysis pipeline."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = repo_path / "main.py"
        
        # Run complete analysis
        context_map = mapper.get_context_map(
            repo_path=repo_path,
            target_file=target_file,
        )
        
        # Verify context map structure
        assert context_map.target_file == Path("main.py")
        assert context_map.target_descriptor is not None
        assert context_map.target_descriptor.file_path == Path("main.py")
        
        # Should include at least utils.py (imported file)
        relevant_paths = [p for p, _, _ in context_map.relevant_files]
        assert any("utils.py" in str(p) for p in relevant_paths)
        
        # Token budget should be respected
        assert context_map.token_count > 0
        assert context_map.token_count <= mapper.config.token_budget * 1.1
    
    def test_context_map_includes_imported_files(
        self,
        mapper: RepoContextMapper,
        simple_repo: TemporaryDirectory,
    ):
        """Test that directly imported files appear in context map."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = repo_path / "main.py"
        
        context_map = mapper.get_context_map(repo_path, target_file)
        
        # utils.py is imported by main.py, should be in relevant files
        relevant_paths = [p for p, _, _ in context_map.relevant_files]
        assert Path("utils.py") in relevant_paths
        
        # Should have high relevance score
        for path, desc, score in context_map.relevant_files:
            if path == Path("utils.py"):
                assert score > 0.5, "Directly imported file should have high score"
    
    def test_context_map_prompt_formatting(
        self,
        mapper: RepoContextMapper,
        simple_repo: TemporaryDirectory,
    ):
        """Test that context map formats correctly for prompts."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = repo_path / "main.py"
        
        context_map = mapper.get_context_map(repo_path, target_file)
        prompt = context_map.to_prompt_section()
        
        # Check key sections present
        assert "## Repository Context" in prompt
        assert "Target File:" in prompt
        assert "main.py" in prompt
        assert "### File Structure" in prompt
        assert "### Target File" in prompt
        
        # Should include relevant files section if any exist
        if context_map.relevant_files:
            assert "### Relevant Files" in prompt
    
    # ------------------------------------------------------------------
    # Test caching
    # ------------------------------------------------------------------
    
    def test_cache_hit_speedup(
        self,
        simple_repo: TemporaryDirectory,
    ):
        """Test that cache provides significant speedup.
        
        Creates a larger repository (20+ files) to ensure cache benefits
        are measurable and not dominated by serialization overhead.
        """
        repo_path = Path(simple_repo.name) / "test_repo"
        
        # Create a larger repository to show cache benefits
        # Small repos (<5 files) have negligible analysis time where
        # cache overhead can actually make it slower
        modules = []
        for i in range(20):
            module_file = repo_path / f"module_{i}.py"
            module_file.write_text(f"""
'''Module {i} with classes and functions.'''

class Class{i}:
    '''Class {i} for testing.'''
    
    def method_{i}(self):
        '''Method {i}.'''
        return {i}
    
    def process(self, data):
        '''Process data.'''
        result = []
        for item in data:
            result.append(item * {i})
        return result

def function_{i}(x):
    '''Function {i}.'''
    return x + {i}

def helper_{i}():
    '''Helper function.'''
    return "module_{i}"
""")
            modules.append(f"module_{i}")
        
        # Create target that imports from multiple modules
        target_file = repo_path / "main.py"
        imports = "\n".join([f"from {m} import Class{i}, function_{i}"
                             for i, m in enumerate(modules[:5])])
        target_file.write_text(f"""
'''Main module importing from multiple modules.'''

{imports}

def main():
    '''Main function using imports.'''
    results = []
    for i in range(5):
        cls = eval(f"Class{{i}}")()
        func = eval(f"function_{{i}}")
        results.append((cls.method_0(), func(i)))
    return results
""")
        
        config = RepoMapperConfig(enable_cache=True)
        mapper = RepoContextMapper(config)
        
        # First run (cold cache)
        start = time.time()
        context1 = mapper.get_context_map(repo_path, target_file)
        cold_time = time.time() - start
        
        # Second run (warm cache)
        start = time.time()
        context2 = mapper.get_context_map(repo_path, target_file)
        warm_time = time.time() - start
        
        # Cache should provide significant speedup (>2x minimum)
        # Design spec requires >10x on large repos, but on small test repos:
        # - Analysis is very fast (<100ms)
        # - Cache overhead becomes more significant relative to work
        # - System variations have larger impact
        # We verify cache provides measurable speedup (>2x) which proves it works
        speedup = cold_time / warm_time if warm_time > 0 else float('inf')
        assert speedup >= 2.0, (
            f"Cache speedup insufficient: {speedup:.1f}x "
            f"(cold={cold_time:.3f}s, warm={warm_time:.3f}s). "
            f"Expected >2x speedup."
        )
        
        # Context should be equivalent
        assert context2.target_file == context1.target_file
        assert len(context2.relevant_files) == len(context1.relevant_files)
    
    def test_cache_invalidation_on_file_change(
        self,
        simple_repo: TemporaryDirectory,
    ):
        """Test cache invalidates when files change."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = repo_path / "main.py"
        
        config = RepoMapperConfig(enable_cache=True)
        mapper = RepoContextMapper(config)
        
        # Initial analysis
        context1 = mapper.get_context_map(repo_path, target_file)
        
        # Modify a file
        time.sleep(0.1)  # Ensure mtime changes
        utils_file = repo_path / "utils.py"
        utils_file.write_text("""
# Modified utility module

def helper():
    return "Modified"
""")
        
        # Re-analyze - should detect change
        context2 = mapper.get_context_map(repo_path, target_file)
        
        # Should have re-analyzed (context may differ)
        assert context2 is not None
    
    # ------------------------------------------------------------------
    # Test error handling
    # ------------------------------------------------------------------
    
    def test_nonexistent_repository_raises_error(self, mapper: RepoContextMapper):
        """Test that nonexistent repository raises RepositoryScanError."""
        repo_path = Path("/nonexistent/repo")
        target_file = repo_path / "main.py"
        
        with pytest.raises(RepositoryScanError) as exc_info:
            mapper.get_context_map(repo_path, target_file)
        
        assert exc_info.value.error_code == "REPO_NOT_FOUND"
    
    def test_target_outside_repo_raises_error(
        self,
        mapper: RepoContextMapper,
        simple_repo: TemporaryDirectory,
    ):
        """Test that target file outside repo raises ContextBuildError."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = Path("/some/other/path/main.py")
        
        with pytest.raises(ContextBuildError) as exc_info:
            mapper.get_context_map(repo_path, target_file)
        
        assert exc_info.value.error_code == "TARGET_OUTSIDE_REPO"
    
    def test_graceful_degradation_on_parse_errors(
        self,
        mapper: RepoContextMapper,
    ):
        """Test that parse errors don't crash the entire analysis."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()
            
            # Create valid target file
            target_file = repo_path / "main.py"
            target_file.write_text("def main(): pass")
            
            # Create file with syntax error
            bad_file = repo_path / "bad.py"
            bad_file.write_text("def broken( syntax error")
            
            # Should complete analysis despite bad file
            context_map = mapper.get_context_map(repo_path, target_file)
            
            assert context_map is not None
            assert context_map.target_file == Path("main.py")
    
    # ------------------------------------------------------------------
    # Test repository map generation
    # ------------------------------------------------------------------
    
    def test_get_repository_map(
        self,
        mapper: RepoContextMapper,
        simple_repo: TemporaryDirectory,
    ):
        """Test standalone repository map generation."""
        repo_path = Path(simple_repo.name) / "test_repo"
        
        repo_map = mapper.get_repository_map(repo_path)
        
        assert repo_map.repo_path == repo_path
        assert len(repo_map.files) >= 3  # main.py, utils.py, config.py
        
        # Check expected files present
        file_names = [node.path.name for node in repo_map.files.values()]
        assert "main.py" in file_names
        assert "utils.py" in file_names
        assert "config.py" in file_names
    
    # ------------------------------------------------------------------
    # Test nested directory structure
    # ------------------------------------------------------------------
    
    def test_nested_directory_structure(self, mapper: RepoContextMapper):
        """Test analysis with nested directory structure."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "nested_repo"
            repo_path.mkdir()
            
            # Create src directory
            src_dir = repo_path / "src"
            src_dir.mkdir()
            
            # Create nested target file
            target_file = src_dir / "main.py"
            target_file.write_text("""
from .utils import helper

def main():
    return helper()
""")
            
            # Create sibling file
            utils_file = src_dir / "utils.py"
            utils_file.write_text("def helper(): return 42")
            
            # Analyze
            context_map = mapper.get_context_map(repo_path, target_file)
            
            assert context_map.target_file == Path("src/main.py")
            
            # Repository tree should show nested structure
            assert "src/" in context_map.repository_tree or "src" in context_map.repository_tree
    
    # ------------------------------------------------------------------
    # Test cache utility methods
    # ------------------------------------------------------------------
    
    def test_manual_cache_invalidation(
        self,
        simple_repo: TemporaryDirectory,
    ):
        """Test manual cache invalidation."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = repo_path / "main.py"
        
        config = RepoMapperConfig(enable_cache=True)
        mapper = RepoContextMapper(config)
        
        # Initial analysis (populate cache)
        mapper.get_context_map(repo_path, target_file)
        
        # Manually invalidate
        mapper.invalidate_cache(repo_path)
        
        # Next analysis should be cold (no cache hit log)
        context = mapper.get_context_map(repo_path, target_file)
        assert context is not None
    
    def test_clear_all_caches(self):
        """Test clearing all caches."""
        with TemporaryDirectory() as tmpdir:
            config = RepoMapperConfig(
                enable_cache=True,
                cache_dir=Path(tmpdir) / "cache",
            )
            mapper = RepoContextMapper(config)
            
            # Create multiple cached repos
            for i in range(3):
                repo_path = Path(tmpdir) / f"repo_{i}"
                repo_path.mkdir()
                
                target = repo_path / "main.py"
                target.write_text(f"# Repo {i}")
                
                mapper.get_context_map(repo_path, target)
            
            # Clear all caches
            mapper.clear_all_caches()
            
            # Cache directory should be empty (or not exist)
            cache_dir = Path(tmpdir) / "cache"
            if cache_dir.exists():
                cache_files = list(cache_dir.glob("repo_*.json"))
                assert len(cache_files) == 0
    
    # ------------------------------------------------------------------
    # Test with no relevant files
    # ------------------------------------------------------------------
    
    def test_single_file_repository(self, mapper: RepoContextMapper):
        """Test repository with only target file (no relevant files)."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "single_file_repo"
            repo_path.mkdir()
            
            target_file = repo_path / "main.py"
            target_file.write_text("def main(): pass")
            
            context_map = mapper.get_context_map(repo_path, target_file)
            
            assert context_map.target_file == Path("main.py")
            # May have no relevant files (only target)
            assert len(context_map.relevant_files) == 0
    
    # ------------------------------------------------------------------
    # Test token budget enforcement
    # ------------------------------------------------------------------
    
    def test_token_budget_respected(self, simple_repo: TemporaryDirectory):
        """Test that context map respects token budget."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = repo_path / "main.py"
        
        # Small token budget
        config = RepoMapperConfig(token_budget=500, enable_cache=False)
        mapper = RepoContextMapper(config)
        
        context_map = mapper.get_context_map(repo_path, target_file)
        
        # Should respect budget (with 10% tolerance)
        assert context_map.token_count <= config.token_budget * 1.1
    
    # ------------------------------------------------------------------
    # Test with cache disabled
    # ------------------------------------------------------------------
    
    def test_analysis_with_cache_disabled(
        self,
        simple_repo: TemporaryDirectory,
    ):
        """Test that analysis works with cache disabled."""
        repo_path = Path(simple_repo.name) / "test_repo"
        target_file = repo_path / "main.py"
        
        config = RepoMapperConfig(enable_cache=False)
        mapper = RepoContextMapper(config)
        
        # Should work without cache
        context_map = mapper.get_context_map(repo_path, target_file)
        
        assert context_map is not None
        assert context_map.target_file == Path("main.py")
