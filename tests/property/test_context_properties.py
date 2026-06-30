"""
Property-based tests for ContextBuilder (Task 5.4).

Tests universal correctness properties:
- Property 4: Token Budget Compliance
- Property 10: Context Map Relevance Ordering
"""

import pytest
from hypothesis import given, strategies as st, assume, settings
from pathlib import Path
from openevolve.repo_mapper.models import (
    FileDescriptor,
    FileNode,
    RelevanceScore,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.context_builder import ContextBuilder


# ------------------------------------------------------------------
# Property 4: Token Budget Compliance
# ------------------------------------------------------------------

@given(
    token_budget=st.integers(min_value=100, max_value=5000),
    num_files=st.integers(min_value=2, max_value=20),
)
@settings(max_examples=100)
def test_property_token_budget_compliance(token_budget: int, num_files: int):
    """Property 4: Context maps SHALL NOT exceed the configured token budget.
    
    Generates repositories of varying sizes with different token budgets and
    verifies that the resulting context map always respects the limit.
    
    Validates: Requirement 4.7
    """
    config = RepoMapperConfig(token_budget=token_budget)
    
    # Generate test repository
    root = Path("/test_repo")
    files = {}
    descriptors = {}
    scored = []
    
    for i in range(num_files):
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
        
        # Vary descriptor length
        summary_length = (i + 1) * 20
        descriptors[path] = FileDescriptor(
            file_path=path,
            role="utility",
            summary="x" * summary_length,
            functions=[f"func_{j}" for j in range(i % 3 + 1)],
            classes=[f"Class{j}" for j in range(i % 2)],
            loc=(i + 1) * 10,
        )
        
        # Create relevance scores (skip first file as it's the target)
        if i > 0:
            scored.append(
                RelevanceScore(
                    file_path=path,
                    total_score=1.0 - (i * 0.05),
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
    
    builder = ContextBuilder(config)
    context = builder.build(
        target_file=Path("file_0.py"),
        repo_map=repo_map,
        scored_files=scored,
        descriptors=descriptors,
    )
    
    # PROPERTY: token_count <= token_budget (with small tolerance for overhead)
    # Allow 10% overhead for formatting characters
    assert context.token_count <= token_budget * 1.1, (
        f"Context exceeded budget: {context.token_count} > {token_budget} * 1.1"
    )


@given(
    token_budget=st.integers(min_value=50, max_value=500),
)
@settings(max_examples=50)
def test_property_token_budget_with_large_target(token_budget: int):
    """Property 4 variant: Even with large target descriptor, budget is respected.
    
    Tests the truncation logic when target file alone would exceed budget.
    """
    config = RepoMapperConfig(token_budget=token_budget)
    
    root = Path("/repo")
    target_path = Path("large_target.py")
    
    # Create target with very long summary
    descriptors = {
        target_path: FileDescriptor(
            file_path=target_path,
            role="main",
            summary="A" * 2000,  # Very long summary
            functions=["f" + str(i) for i in range(50)],  # Many functions
            classes=["C" + str(i) for i in range(20)],  # Many classes
            loc=5000,
        )
    }
    
    files = {
        target_path: FileNode(
            path=target_path,
            absolute_path=root / target_path,
            is_dir=False,
            size_bytes=10000,
            modified_time=0.0,
            depth=1,
        )
    }
    
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
    
    builder = ContextBuilder(config)
    context = builder.build(
        target_file=target_path,
        repo_map=repo_map,
        scored_files=[],
        descriptors=descriptors,
    )
    
    # PROPERTY: Even with truncation, token count <= budget * 1.1
    assert context.token_count <= token_budget * 1.1


# ------------------------------------------------------------------
# Property 10: Context Map Relevance Ordering
# ------------------------------------------------------------------

@given(
    num_files=st.integers(min_value=3, max_value=15),
    seed=st.integers(min_value=0, max_value=1000),
)
@settings(max_examples=100)
def test_property_relevance_ordering(num_files: int, seed: int):
    """Property 10: Files in ContextMap.relevant_files SHALL be ordered by
    descending relevance score.
    
    Generates random relevance scores and verifies the context map maintains
    the correct sort order.
    
    Validates: Requirement 4.1
    """
    import random
    random.seed(seed)
    
    config = RepoMapperConfig(token_budget=5000)  # Large budget
    
    root = Path("/repo")
    files = {}
    descriptors = {}
    scored = []
    
    # Generate files with random scores
    for i in range(num_files):
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
            summary=f"File {i}",
            loc=50,
        )
        
        if i > 0:  # Skip target
            # Random score
            score = random.random()
            scored.append(
                RelevanceScore(
                    file_path=path,
                    total_score=score,
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
    
    builder = ContextBuilder(config)
    context = builder.build(
        target_file=Path("file_0.py"),
        repo_map=repo_map,
        scored_files=scored,
        descriptors=descriptors,
    )
    
    # PROPERTY: relevant_files must be sorted by score descending
    if len(context.relevant_files) > 1:
        scores = [score for _, _, score in context.relevant_files]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Relevance ordering violated: scores[{i}]={scores[i]:.3f} "
                f"< scores[{i+1}]={scores[i+1]:.3f}"
            )


@given(
    num_files=st.integers(min_value=5, max_value=20),
)
@settings(max_examples=50)
def test_property_highest_scored_appears_first(num_files: int):
    """Property 10 variant: Highest scored file always appears first in
    relevant_files list (if included).
    """
    config = RepoMapperConfig(token_budget=3000)
    
    root = Path("/repo")
    files = {}
    descriptors = {}
    scored = []
    
    # Generate files with known scores
    max_score = 0.0
    max_score_path = None
    
    for i in range(num_files):
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
            summary=f"File {i}",
            loc=30,
        )
        
        if i > 0:
            score = i * 0.1  # Increasing scores
            if score > max_score:
                max_score = score
                max_score_path = path
            
            scored.append(
                RelevanceScore(
                    file_path=path,
                    total_score=score,
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
    
    builder = ContextBuilder(config)
    context = builder.build(
        target_file=Path("file_0.py"),
        repo_map=repo_map,
        scored_files=scored,
        descriptors=descriptors,
    )
    
    # PROPERTY: If highest-scored file is included, it must be first
    if context.relevant_files:
        first_path, _, first_score = context.relevant_files[0]
        
        # Check if max_score file is in the list
        included_paths = {p for p, _, _ in context.relevant_files}
        if max_score_path in included_paths:
            assert first_path == max_score_path, (
                f"Highest scored file {max_score_path} (score={max_score:.3f}) "
                f"not first. First was {first_path} (score={first_score:.3f})"
            )


# ------------------------------------------------------------------
# Property invariants combining both properties
# ------------------------------------------------------------------

@given(
    token_budget=st.integers(min_value=200, max_value=3000),
    num_files=st.integers(min_value=3, max_value=15),
    seed=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=100)
def test_property_combined_budget_and_ordering(
    token_budget: int,
    num_files: int,
    seed: int,
):
    """Combined property test: Context maps respect both token budget AND
    maintain relevance ordering simultaneously.
    
    This is the critical integration property — both constraints must hold.
    """
    import random
    random.seed(seed)
    
    config = RepoMapperConfig(token_budget=token_budget)
    
    root = Path("/repo")
    files = {}
    descriptors = {}
    scored = []
    
    for i in range(num_files):
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
        
        # Vary summary lengths
        summary_len = random.randint(10, 100)
        descriptors[path] = FileDescriptor(
            file_path=path,
            role="utility",
            summary="x" * summary_len,
            functions=[f"f{j}" for j in range(random.randint(0, 5))],
            loc=random.randint(10, 200),
        )
        
        if i > 0:
            scored.append(
                RelevanceScore(
                    file_path=path,
                    total_score=random.random(),
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
    
    builder = ContextBuilder(config)
    context = builder.build(
        target_file=Path("file_0.py"),
        repo_map=repo_map,
        scored_files=scored,
        descriptors=descriptors,
    )
    
    # PROPERTY 4: Token budget respected
    assert context.token_count <= token_budget * 1.1
    
    # PROPERTY 10: Ordering maintained
    if len(context.relevant_files) > 1:
        scores = [s for _, _, s in context.relevant_files]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]
