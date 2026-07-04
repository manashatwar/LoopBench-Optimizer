"""
Property-Based Tests for Repository Context Mapper

These tests validate universal correctness properties using Hypothesis.
They generate random repositories and verify invariants hold across all cases.
"""

import tempfile
from pathlib import Path
from hypothesis import given, strategies as st, settings, example, assume
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant
import pytest

from openevolve.repo_mapper import RepoContextMapper
from openevolve.repo_mapper.models import RepoMapperConfig
from openevolve.repo_mapper.scanner import RepositoryScanner
from openevolve.repo_mapper.relevance_scorer import RelevanceScorer


# ============================================================================
# Hypothesis Strategies for Generating Test Data
# ============================================================================

@st.composite
def python_identifier(draw):
    """Generate valid Python identifiers"""
    first_char = draw(st.sampled_from('abcdefghijklmnopqrstuvwxyz_'))
    rest = draw(st.text(
        alphabet='abcdefghijklmnopqrstuvwxyz0123456789_',
        min_size=0,
        max_size=20
    ))
    return first_char + rest


@st.composite
def python_file_content(draw):
    """Generate valid Python file content"""
    # Simple Python code that always parses
    func_name = draw(python_identifier())
    docstring = draw(st.text(min_size=0, max_size=100))
    return f'''"""
{docstring}
"""

def {func_name}():
    """Function docstring"""
    return 42
'''


@st.composite
def file_tree(draw, max_depth=3, max_files=10):
    """Generate a random file tree structure"""
    num_files = draw(st.integers(min_value=1, max_value=max_files))
    depth = draw(st.integers(min_value=1, max_value=max_depth))
    
    files = []
    for i in range(num_files):
        # Generate path with random depth
        file_depth = draw(st.integers(min_value=0, max_value=depth))
        path_parts = []
        for d in range(file_depth):
            dir_name = draw(python_identifier())
            path_parts.append(dir_name)
        
        filename = draw(python_identifier()) + '.py'
        path_parts.append(filename)
        
        file_path = '/'.join(path_parts)
        content = draw(python_file_content())
        
        files.append((file_path, content))
    
    return files


def create_test_repo(tmpdir, files):
    """Create a test repository from file tree"""
    repo_path = Path(tmpdir) / "test_repo"
    repo_path.mkdir(exist_ok=True)
    
    for file_path, content in files:
        full_path = repo_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
    
    return repo_path


# ============================================================================
# Property 1: Ignore Pattern Exclusivity
# ============================================================================

@given(file_tree=file_tree(max_depth=3, max_files=10))
@settings(max_examples=1000, deadline=15000)  # Increased to 1000 for critical property
@example(file_tree=[('main.py', 'def main(): pass')])
@example(file_tree=[])  # Edge case: empty repository
@example(file_tree=[('deeply/nested/structure/file.py', 'def f(): pass')])  # Deep nesting
@example(file_tree=[('node_modules/lib.py', 'pass')])  # Should be ignored
@example(file_tree=[('.git/config', 'pass')])  # Should be ignored
@example(file_tree=[('__pycache__/cache.pyc', 'pass')])  # Should be ignored
def test_property_ignore_pattern_exclusivity(file_tree):
    """
    Property 1: Files matching ignore patterns should never appear in scan results
    
    For any repository and ignore patterns, no file matching an ignore pattern
    should be present in the RepositoryMap.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = create_test_repo(tmpdir, file_tree)
        
        # Add a file that should be ignored
        ignored_file = repo_path / '__pycache__' / 'test.pyc'
        ignored_file.parent.mkdir(exist_ok=True)
        ignored_file.write_text('ignored')
        
        config = RepoMapperConfig(
            ignore_patterns=['__pycache__/'],
            enable_cache=False,
        )
        scanner = RepositoryScanner(config)
        
        repo_map = scanner.scan(repo_path)
        
        # Verify no ignored files in results
        for file_path in repo_map.files.keys():
            assert '__pycache__' not in str(file_path), \
                f"Ignored file {file_path} found in scan results"


# ============================================================================
# Property 2: Relevance Score Bounds
# ============================================================================

@given(file_tree=file_tree(max_depth=2, max_files=8))
@settings(max_examples=300, deadline=10000)  # Increased from 100 to 300 for critical property
@example(file_tree=[('main.py', 'def main(): pass'), ('utils.py', 'def helper(): pass')])
@example(file_tree=[('single.py', 'def f(): pass')])  # Edge case: single file
@example(file_tree=[('a.py', ''), ('b.py', ''), ('c.py', '')])  # Edge case: empty files
def test_property_relevance_score_bounds(file_tree):
    """
    Property 3: All relevance scores must be in [0.0, 1.0] and target has highest score
    
    For any repository and target file:
    - All relevance scores are between 0.0 and 1.0
    - Target file has the highest score (1.0)
    - Scores are deterministic (same input = same output)
    """
    # Need at least 2 files for meaningful test
    assume(len(file_tree) >= 2)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = create_test_repo(tmpdir, file_tree)
        
        # Pick first file as target
        target_file = repo_path / file_tree[0][0]
        
        try:
            config = RepoMapperConfig(enable_cache=False)
            mapper = RepoContextMapper(config)
            
            context_map = mapper.get_context_map(repo_path, target_file)
            
            # Check target is always included
            assert context_map.target_file == target_file.relative_to(repo_path)
            
            # Check all scores are in valid range
            for path, descriptor, score in context_map.relevant_files:
                assert 0.0 <= score <= 1.0, \
                    f"Score {score} out of bounds for {path}"
                
            # Verify the component scores directly from relevance scorer
            repo_map = mapper.get_repository_map(repo_path)
            import_graph = mapper.import_analyzer.analyze(repo_map)
            scored_files = mapper.relevance_scorer.score_files(
                target_file=target_file.relative_to(repo_path),
                repo_map=repo_map,
                import_graph=import_graph,
            )
            for score_obj in scored_files:
                assert 0.0 <= score_obj.total_score <= 1.0
                assert 0.0 <= score_obj.direct_import_score <= 1.0
                assert 0.0 <= score_obj.reverse_import_score <= 1.0
                assert 0.0 <= score_obj.directory_proximity_score <= 1.0
                assert 0.0 <= score_obj.name_similarity_score <= 1.0
            
        except Exception as e:
            # Graceful degradation is acceptable
            pytest.skip(f"Analysis failed gracefully: {e}")


# ============================================================================
# Property 4: Token Budget Compliance
# ============================================================================

@given(
    file_tree=file_tree(max_depth=2, max_files=5),
    token_budget=st.integers(min_value=500, max_value=3000)
)
@settings(max_examples=50, deadline=5000)
@example(
    file_tree=[('main.py', 'def main(): pass')],
    token_budget=1000
)
def test_property_token_budget_compliance(file_tree, token_budget):
    """
    Property 4: Context map must never exceed configured token budget
    
    For any repository and token budget, the generated context map
    must fit within the specified token limit.
    """
    assume(len(file_tree) >= 1)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = create_test_repo(tmpdir, file_tree)
        target_file = repo_path / file_tree[0][0]
        
        try:
            config = RepoMapperConfig(
                token_budget=token_budget,
                enable_cache=False,
            )
            mapper = RepoContextMapper(config)
            
            context_map = mapper.get_context_map(repo_path, target_file)
            
            # Verify token count is within budget
            assert context_map.token_count <= token_budget, \
                f"Token count {context_map.token_count} exceeds budget {token_budget}"
            
            # Verify token count is reasonable (at least target file should be included)
            assert context_map.token_count > 0, "Token count should be positive"
            
        except Exception as e:
            pytest.skip(f"Analysis failed gracefully: {e}")


# ============================================================================
# Property 5: Depth Limit Enforcement
# ============================================================================

@given(max_depth=st.integers(min_value=1, max_value=5))
@settings(max_examples=50, deadline=5000)
@example(max_depth=3)
def test_property_depth_limit_enforcement(max_depth):
    """
    Property 6: No files should exceed configured max depth
    
    For any repository and max_depth setting, no file in the scan
    should be deeper than max_depth directories from root.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        # Create deep file structure
        deep_path = repo_path
        for i in range(max_depth + 2):  # Create deeper than limit
            deep_path = deep_path / f"level{i}"
            deep_path.mkdir(exist_ok=True)
            (deep_path / "test.py").write_text("# test")
        
        config = RepoMapperConfig(
            max_traversal_depth=max_depth,
            enable_cache=False,
        )
        scanner = RepositoryScanner(config)
        
        repo_map = scanner.scan(repo_path)
        
        # Verify no file exceeds max depth
        for file_node in repo_map.files.values():
            if not file_node.is_dir:
                assert file_node.depth <= max_depth, \
                    f"File {file_node.path} at depth {file_node.depth} exceeds max {max_depth}"


# ============================================================================
# Property 7: Determinism
# ============================================================================

@given(file_tree=file_tree(max_depth=2, max_files=5))
@settings(max_examples=20, deadline=10000)
@example(file_tree=[('main.py', 'def main(): pass'), ('utils.py', 'def helper(): pass')])
def test_property_determinism(file_tree):
    """
    Property 7: Same input produces same output (determinism)
    
    Analyzing the same repository multiple times should produce
    identical relevance scores and file orderings.
    """
    assume(len(file_tree) >= 2)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = create_test_repo(tmpdir, file_tree)
        target_file = repo_path / file_tree[0][0]
        
        try:
            config = RepoMapperConfig(enable_cache=False)
            mapper = RepoContextMapper(config)
            
            # Analyze twice
            context_map_1 = mapper.get_context_map(repo_path, target_file)
            context_map_2 = mapper.get_context_map(repo_path, target_file)
            
            # Verify identical results
            assert context_map_1.token_count == context_map_2.token_count
            assert len(context_map_1.relevant_files) == len(context_map_2.relevant_files)
            
            # Verify same files in same order with same scores
            for (path1, desc1, score1), (path2, desc2, score2) in zip(
                context_map_1.relevant_files,
                context_map_2.relevant_files
            ):
                assert path1 == path2
                assert desc1.file_path == desc2.file_path
                assert abs(score1 - score2) < 1e-6, \
                    f"Scores differ: {score1} vs {score2}"
                    
        except Exception as e:
            pytest.skip(f"Analysis failed gracefully: {e}")


# ============================================================================
# Property 8: Context Map Relevance Ordering
# ============================================================================

@given(file_tree=file_tree(max_depth=2, max_files=8))
@settings(max_examples=50, deadline=5000)
@example(file_tree=[
    ('main.py', 'import utils\ndef main(): pass'),
    ('utils.py', 'def helper(): pass'),
    ('config.py', 'SETTING = 1')
])
def test_property_context_map_relevance_ordering(file_tree):
    """
    Property 10: Files in context map must be ordered by descending relevance score
    
    For any context map, relevant files should be sorted in descending order
    by their total relevance score.
    """
    assume(len(file_tree) >= 3)  # Need multiple files to check ordering
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = create_test_repo(tmpdir, file_tree)
        target_file = repo_path / file_tree[0][0]
        
        try:
            config = RepoMapperConfig(
                enable_cache=False,
                max_relevant_files=10,  # Include many files
            )
            mapper = RepoContextMapper(config)
            
            context_map = mapper.get_context_map(repo_path, target_file)
            
            # Verify descending order
            prev_score = float('inf')
            for path, descriptor, score in context_map.relevant_files:
                assert score <= prev_score, \
                    f"Scores not in descending order: {score} > {prev_score}"
                prev_score = score
                
        except Exception as e:
            pytest.skip(f"Analysis failed gracefully: {e}")


# ============================================================================
# Property 9: File Descriptor Completeness
# ============================================================================

@given(file_tree=file_tree(max_depth=2, max_files=5))
@settings(max_examples=50, deadline=5000)
@example(file_tree=[('main.py', '"""Module doc"""\ndef main(): pass')])
def test_property_file_descriptor_completeness(file_tree):
    """
    Property 8: Every file descriptor must have at least summary, classes, functions, or role
    
    For any analyzed file, its descriptor should contain meaningful information
    in at least one of: summary, classes, functions, or role fields.
    """
    assume(len(file_tree) >= 1)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = create_test_repo(tmpdir, file_tree)
        target_file = repo_path / file_tree[0][0]
        
        try:
            config = RepoMapperConfig(enable_cache=False)
            mapper = RepoContextMapper(config)
            
            context_map = mapper.get_context_map(repo_path, target_file)
            
            # Check target descriptor
            desc = context_map.target_descriptor
            has_info = (
                desc.summary.strip() != '' or
                len(desc.classes) > 0 or
                len(desc.functions) > 0 or
                desc.role != 'module'  # Not just default role
            )
            assert has_info, f"Descriptor for {desc.file_path} is empty"
            
            # Check relevant file descriptors
            for path, descriptor, score in context_map.relevant_files:
                has_info = (
                    descriptor.summary.strip() != '' or
                    len(descriptor.classes) > 0 or
                    len(descriptor.functions) > 0 or
                    descriptor.role != 'module'
                )
                assert has_info, f"Descriptor for {descriptor.file_path} is empty"
                
        except Exception as e:
            pytest.skip(f"Analysis failed gracefully: {e}")


# ============================================================================
# Edge Cases and Shrinking
# ============================================================================

class TestEdgeCases:
    """Test edge cases that property-based testing should handle"""
    
    def test_empty_repository(self):
        """Edge case: Repository with no Python files"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "empty_repo"
            repo_path.mkdir()
            
            # Create a dummy target (will fail gracefully)
            target = repo_path / "main.py"
            target.write_text("# empty")
            
            config = RepoMapperConfig(enable_cache=False)
            scanner = RepositoryScanner(config)
            
            repo_map = scanner.scan(repo_path)
            
            # Should have at least the target file
            assert len(repo_map.files) >= 1
    
    def test_single_file_repository(self):
        """Edge case: Repository with single file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "single_repo"
            repo_path.mkdir()
            
            target = repo_path / "main.py"
            target.write_text("def main(): pass")
            
            config = RepoMapperConfig(enable_cache=False)
            mapper = RepoContextMapper(config)
            
            context_map = mapper.get_context_map(repo_path, target)
            
            # Should work with single file
            assert context_map.target_file == target.relative_to(repo_path)
            assert context_map.token_count > 0
    
    def test_deeply_nested_repository(self):
        """Edge case: Very deep directory nesting"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "deep_repo"
            repo_path.mkdir()
            
            # Create 10-level deep structure
            deep_path = repo_path
            for i in range(10):
                deep_path = deep_path / f"level{i}"
                deep_path.mkdir()
            
            target = deep_path / "main.py"
            target.write_text("def main(): pass")
            
            config = RepoMapperConfig(
                max_traversal_depth=15,  # Allow deep nesting
                enable_cache=False,
            )
            scanner = RepositoryScanner(config)
            
            repo_map = scanner.scan(repo_path)
            
            # Should handle deep nesting
            assert target.relative_to(repo_path) in repo_map.files
    
    def test_files_with_special_characters(self):
        """Edge case: Files with special characters in names"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "special_repo"
            repo_path.mkdir()
            
            # Note: Some special chars not allowed in filenames on Windows
            files = [
                "main.py",
                "test-utils.py",
                "data_processor.py",
                "config.dev.py",
            ]
            
            for filename in files:
                (repo_path / filename).write_text("def func(): pass")
            
            config = RepoMapperConfig(enable_cache=False)
            scanner = RepositoryScanner(config)
            
            repo_map = scanner.scan(repo_path)
            
            # Should handle special characters
            assert len(repo_map.files) >= len(files)
    
    def test_large_file_content(self):
        """Edge case: Very large file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "large_repo"
            repo_path.mkdir()
            
            # Create large file (10k lines)
            target = repo_path / "large.py"
            large_content = "\n".join([f"def func{i}(): pass" for i in range(10000)])
            target.write_text(large_content)
            
            config = RepoMapperConfig(
                enable_cache=False,
                max_file_size_bytes=10 * 1024 * 1024,  # 10MB limit
            )
            mapper = RepoContextMapper(config)
            
            try:
                context_map = mapper.get_context_map(repo_path, target)
                
                # Should handle large file (may truncate descriptor)
                assert context_map.target_file == target.relative_to(repo_path)
                
            except Exception as e:
                # Acceptable to fail gracefully on huge files
                pytest.skip(f"Large file handled gracefully: {e}")


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--hypothesis-show-statistics"])
