"""
Property-based tests for CacheManager (Task 6.5).

Tests universal correctness property:
- Property 5: Cache Validity Correctness
"""

import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, strategies as st, settings, assume

from openevolve.repo_mapper.models import (
    FileDescriptor,
    FileNode,
    ImportGraph,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.cache_manager import CacheEntry, CacheManager


# ------------------------------------------------------------------
# Property 5: Cache Validity Correctness
# ------------------------------------------------------------------

@given(
    num_files=st.integers(min_value=1, max_value=10),
    modify_file_index=st.integers(min_value=0, max_value=9),
)
@settings(max_examples=50, deadline=None)
def test_property_cache_validity_on_modification(
    num_files: int,
    modify_file_index: int,
):
    """Property 5: Cache SHALL be invalid if any tracked file is modified.
    
    Generates repositories with multiple files, modifies one, and verifies
    the cache correctly detects the modification.
    
    Validates: Requirement 7.2, 7.3, 7.4
    """
    # Ensure we modify a valid file index
    assume(modify_file_index < num_files)
    
    with TemporaryDirectory() as tmpdir:
        # Create test repository with real files
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        files = {}
        file_mtimes = {}
        
        for i in range(num_files):
            file_name = f"file_{i}.py"
            file_path = repo_path / file_name
            file_path.write_text(f"# File {i}")
            
            mtime = file_path.stat().st_mtime
            rel_path = Path(file_name)
            
            files[rel_path] = FileNode(
                path=rel_path,
                absolute_path=file_path,
                is_dir=False,
                size_bytes=100,
                modified_time=mtime,
                depth=1,
            )
            file_mtimes[rel_path] = mtime
        
        # Create cache entry
        repo_map = RepositoryMap(
            repo_path=repo_path,
            root_node=FileNode(Path("."), repo_path, True, 0, 0.0, 0),
            files=files,
            scan_timestamp=time.time(),
        )
        
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=repo_map,
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes=file_mtimes,
        )
        
        # Create cache manager
        config = RepoMapperConfig(enable_cache=True, cache_dir=Path(tmpdir) / "cache")
        manager = CacheManager(config)
        
        # PROPERTY: Cache should be valid before modification
        assert manager.is_valid(entry, repo_path), "Cache should be valid initially"
        
        # Modify one file
        modify_file = repo_path / f"file_{modify_file_index}.py"
        time.sleep(0.1)  # Ensure mtime changes
        modify_file.write_text(f"# Modified file {modify_file_index}")
        
        # PROPERTY: Cache should be invalid after modification
        assert not manager.is_valid(entry, repo_path), (
            f"Cache should be invalid after modifying file_{modify_file_index}.py"
        )


@given(
    num_initial_files=st.integers(min_value=1, max_value=8),
)
@settings(max_examples=50, deadline=None)
def test_property_cache_validity_on_new_file(num_initial_files: int):
    """Property 5 variant: Cache SHALL be invalid when new Python files added.
    
    Validates: Requirement 7.4
    """
    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        files = {}
        file_mtimes = {}
        
        # Create initial files
        for i in range(num_initial_files):
            file_name = f"file_{i}.py"
            file_path = repo_path / file_name
            file_path.write_text(f"# File {i}")
            
            mtime = file_path.stat().st_mtime
            rel_path = Path(file_name)
            
            files[rel_path] = FileNode(
                path=rel_path,
                absolute_path=file_path,
                is_dir=False,
                size_bytes=100,
                modified_time=mtime,
                depth=1,
            )
            file_mtimes[rel_path] = mtime
        
        # Create cache entry
        repo_map = RepositoryMap(
            repo_path=repo_path,
            root_node=FileNode(Path("."), repo_path, True, 0, 0.0, 0),
            files=files,
            scan_timestamp=time.time(),
        )
        
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=repo_map,
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes=file_mtimes,
        )
        
        config = RepoMapperConfig(enable_cache=True, cache_dir=Path(tmpdir) / "cache")
        manager = CacheManager(config)
        
        # PROPERTY: Cache valid before adding new file
        assert manager.is_valid(entry, repo_path)
        
        # Add new Python file
        new_file = repo_path / "new_file.py"
        new_file.write_text("# New file")
        
        # PROPERTY: Cache invalid after adding new file
        assert not manager.is_valid(entry, repo_path), (
            "Cache should be invalid after adding new Python file"
        )


@given(
    num_files=st.integers(min_value=1, max_value=5),
    delete_file_index=st.integers(min_value=0, max_value=4),
)
@settings(max_examples=50, deadline=None)
def test_property_cache_validity_on_deletion(
    num_files: int,
    delete_file_index: int,
):
    """Property 5 variant: Cache SHALL be invalid when tracked file deleted.
    
    Validates: Requirement 7.2, 7.3
    """
    assume(delete_file_index < num_files)
    
    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        files = {}
        file_mtimes = {}
        
        for i in range(num_files):
            file_name = f"file_{i}.py"
            file_path = repo_path / file_name
            file_path.write_text(f"# File {i}")
            
            mtime = file_path.stat().st_mtime
            rel_path = Path(file_name)
            
            files[rel_path] = FileNode(
                path=rel_path,
                absolute_path=file_path,
                is_dir=False,
                size_bytes=100,
                modified_time=mtime,
                depth=1,
            )
            file_mtimes[rel_path] = mtime
        
        repo_map = RepositoryMap(
            repo_path=repo_path,
            root_node=FileNode(Path("."), repo_path, True, 0, 0.0, 0),
            files=files,
            scan_timestamp=time.time(),
        )
        
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=repo_map,
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes=file_mtimes,
        )
        
        config = RepoMapperConfig(enable_cache=True, cache_dir=Path(tmpdir) / "cache")
        manager = CacheManager(config)
        
        # PROPERTY: Cache valid before deletion
        assert manager.is_valid(entry, repo_path)
        
        # Delete one file
        delete_file = repo_path / f"file_{delete_file_index}.py"
        delete_file.unlink()
        
        # PROPERTY: Cache invalid after deletion
        assert not manager.is_valid(entry, repo_path), (
            f"Cache should be invalid after deleting file_{delete_file_index}.py"
        )


@settings(max_examples=20, deadline=None)
@given(cache_age_seconds=st.floats(min_value=0.0, max_value=10.0))
def test_property_cache_ttl_enforcement(cache_age_seconds: float):
    """Property 5 variant: Cache SHALL be invalid when older than TTL.
    
    Tests that TTL is properly enforced regardless of cache age.
    
    Validates: Requirement 7.6 (cache TTL)
    """
    with TemporaryDirectory() as tmpdir:
        # Set TTL to 5 seconds
        ttl = 5.0
        config = RepoMapperConfig(
            enable_cache=True,
            cache_dir=Path(tmpdir) / "cache",
            cache_ttl_seconds=ttl,
        )
        manager = CacheManager(config)
        
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        # Create cache entry with specific age
        cache_time = time.time() - cache_age_seconds
        
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path, FileNode(Path("."), repo_path, True, 0, 0.0, 0), {}, 0.0
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=cache_time,
            file_mtimes={},
        )
        
        # PROPERTY: Cache validity depends on age vs TTL
        is_valid = manager.is_valid(entry, repo_path)
        
        if cache_age_seconds > ttl:
            assert not is_valid, f"Cache age {cache_age_seconds}s > TTL {ttl}s, should be invalid"
        else:
            assert is_valid, f"Cache age {cache_age_seconds}s <= TTL {ttl}s, should be valid"


@given(num_files=st.integers(min_value=1, max_value=8))
@settings(max_examples=50, deadline=None)
def test_property_cache_round_trip_consistency(num_files: int):
    """Property: Cache serialization and deserialization preserves data.
    
    Verifies that storing and retrieving cache entries preserves all data.
    """
    with TemporaryDirectory() as tmpdir:
        config = RepoMapperConfig(
            enable_cache=True,
            cache_dir=Path(tmpdir) / "cache",
        )
        manager = CacheManager(config)
        
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        # Create files and descriptors
        files = {}
        descriptors = {}
        file_mtimes = {}
        
        for i in range(num_files):
            file_name = f"file_{i}.py"
            file_path = repo_path / file_name
            file_path.write_text(f"# File {i}")
            
            rel_path = Path(file_name)
            mtime = file_path.stat().st_mtime
            
            files[rel_path] = FileNode(
                rel_path, file_path, False, 100, mtime, 1
            )
            
            descriptors[rel_path] = FileDescriptor(
                rel_path,
                "utility",
                f"Description for file {i}",
                functions=[f"func_{i}"],
                classes=[],
                has_main=False,
                loc=10 + i,
            )
            
            file_mtimes[rel_path] = mtime
        
        # Create original entry
        original = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path,
                FileNode(Path("."), repo_path, True, 0, 0.0, 0),
                files,
                time.time(),
            ),
            import_graph=ImportGraph(),
            descriptors=descriptors,
            cache_time=time.time(),
            file_mtimes=file_mtimes,
        )
        
        # Store and retrieve
        manager.put(original)
        retrieved = manager.get(repo_path)
        
        # PROPERTY: Retrieved entry should match original
        assert retrieved is not None, "Cache should be retrievable"
        assert retrieved.repo_path == original.repo_path
        assert len(retrieved.file_mtimes) == len(original.file_mtimes)
        assert len(retrieved.descriptors) == len(original.descriptors)
        
        # Check descriptors preserved
        for path, desc in original.descriptors.items():
            assert path in retrieved.descriptors
            retrieved_desc = retrieved.descriptors[path]
            assert retrieved_desc.file_path == desc.file_path
            assert retrieved_desc.role == desc.role
            assert retrieved_desc.summary == desc.summary
            assert retrieved_desc.functions == desc.functions
            assert retrieved_desc.loc == desc.loc


@given(
    num_unchanged=st.integers(min_value=1, max_value=5),
    num_modified=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=30, deadline=None)
def test_property_partial_modification_invalidates(
    num_unchanged: int,
    num_modified: int,
):
    """Property 5: Cache SHALL be invalid even if only one file changes.
    
    Tests that modifying even a single file among many invalidates the cache.
    """
    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        total_files = num_unchanged + num_modified
        files = {}
        file_mtimes = {}
        
        # Create all files
        for i in range(total_files):
            file_name = f"file_{i}.py"
            file_path = repo_path / file_name
            file_path.write_text(f"# File {i}")
            
            rel_path = Path(file_name)
            mtime = file_path.stat().st_mtime
            
            files[rel_path] = FileNode(
                rel_path, file_path, False, 100, mtime, 1
            )
            file_mtimes[rel_path] = mtime
        
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path,
                FileNode(Path("."), repo_path, True, 0, 0.0, 0),
                files,
                time.time(),
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes=file_mtimes,
        )
        
        config = RepoMapperConfig(enable_cache=True, cache_dir=Path(tmpdir) / "cache")
        manager = CacheManager(config)
        
        # PROPERTY: Cache valid before any modifications
        assert manager.is_valid(entry, repo_path)
        
        # Modify some files (not all)
        time.sleep(0.1)
        for i in range(num_modified):
            modify_file = repo_path / f"file_{i}.py"
            modify_file.write_text(f"# Modified file {i}")
        
        # PROPERTY: Cache invalid even with partial modifications
        assert not manager.is_valid(entry, repo_path), (
            f"Cache should be invalid when {num_modified}/{total_files} files modified"
        )
