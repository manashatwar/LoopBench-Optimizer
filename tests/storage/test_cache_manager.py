"""
Unit tests for CacheManager (Task 6.4).

Tests cache storage, retrieval, validation, and invalidation logic.
"""

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from openevolve.repo_mapper.models import (
    FileDescriptor,
    FileNode,
    ImportGraph,
    ImportRelation,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.cache_manager import CacheEntry, CacheManager


class TestCacheEntry:
    """Tests for CacheEntry data model (Task 6.1)."""
    
    @pytest.fixture
    def sample_entry(self) -> CacheEntry:
        """Create a sample cache entry for testing."""
        repo_path = Path("/test/repo")
        
        # Simple repo map
        repo_map = RepositoryMap(
            repo_path=repo_path,
            root_node=FileNode(
                path=Path("."),
                absolute_path=repo_path,
                is_dir=True,
                size_bytes=0,
                modified_time=1000.0,
                depth=0,
            ),
            files={
                Path("main.py"): FileNode(
                    path=Path("main.py"),
                    absolute_path=repo_path / "main.py",
                    is_dir=False,
                    size_bytes=100,
                    modified_time=2000.0,
                    depth=1,
                ),
            },
            scan_timestamp=3000.0,
        )
        
        # Simple import graph
        import_graph = ImportGraph()
        import_graph.add_relation(
            ImportRelation(
                source_file=Path("main.py"),
                target_module="utils",
                target_file=Path("utils.py"),
                import_type="relative",
                line_number=5,
            )
        )
        
        # Simple descriptors
        descriptors = {
            Path("main.py"): FileDescriptor(
                file_path=Path("main.py"),
                role="main",
                summary="Main file",
                functions=["main"],
                has_main=True,
                loc=50,
            ),
        }
        
        return CacheEntry(
            repo_path=repo_path,
            repo_map=repo_map,
            import_graph=import_graph,
            descriptors=descriptors,
            cache_time=time.time(),
            file_mtimes={Path("main.py"): 2000.0},
        )
    
    def test_cache_entry_creation(self, sample_entry: CacheEntry):
        """Test creating a cache entry."""
        assert sample_entry.repo_path == Path("/test/repo")
        assert len(sample_entry.repo_map.files) == 1
        assert len(sample_entry.import_graph.relations) == 1
        assert len(sample_entry.descriptors) == 1
        assert sample_entry.cache_format_version == "1.0"
    
    def test_cache_entry_serialization(self, sample_entry: CacheEntry):
        """Test serializing cache entry to dict."""
        data = sample_entry.to_dict()
        
        assert data["cache_format_version"] == "1.0"
        assert data["repo_path"] == "/test/repo"
        assert "repo_map" in data
        assert "import_graph" in data
        assert "descriptors" in data
        assert "file_mtimes" in data
    
    def test_cache_entry_deserialization(self, sample_entry: CacheEntry):
        """Test deserializing cache entry from dict."""
        data = sample_entry.to_dict()
        restored = CacheEntry.from_dict(data)
        
        assert restored.repo_path == sample_entry.repo_path
        assert len(restored.repo_map.files) == len(sample_entry.repo_map.files)
        assert len(restored.import_graph.relations) == len(sample_entry.import_graph.relations)
        assert len(restored.descriptors) == len(sample_entry.descriptors)
        assert restored.cache_format_version == sample_entry.cache_format_version
    
    def test_cache_entry_round_trip(self, sample_entry: CacheEntry):
        """Test serialization and deserialization round trip."""
        data = sample_entry.to_dict()
        restored = CacheEntry.from_dict(data)
        
        # Verify key data preserved
        assert restored.file_mtimes == sample_entry.file_mtimes
        assert restored.cache_time == sample_entry.cache_time


class TestCacheManager:
    """Tests for CacheManager (Task 6.2 and 6.3)."""
    
    @pytest.fixture
    def temp_cache_dir(self) -> TemporaryDirectory:
        """Create temporary cache directory."""
        return TemporaryDirectory()
    
    @pytest.fixture
    def config(self, temp_cache_dir: TemporaryDirectory) -> RepoMapperConfig:
        """Configuration with cache enabled and temp directory."""
        return RepoMapperConfig(
            enable_cache=True,
            cache_dir=Path(temp_cache_dir.name),
            cache_ttl_seconds=3600.0,
        )
    
    @pytest.fixture
    def cache_manager(self, config: RepoMapperConfig) -> CacheManager:
        """Create cache manager instance."""
        return CacheManager(config)
    
    @pytest.fixture
    def sample_entry(self) -> CacheEntry:
        """Sample cache entry for testing."""
        repo_path = Path("/test/repo")
        repo_map = RepositoryMap(
            repo_path=repo_path,
            root_node=FileNode(Path("."), repo_path, True, 0, 1000.0, 0),
            files={
                Path("test.py"): FileNode(
                    Path("test.py"),
                    repo_path / "test.py",
                    False,
                    100,
                    2000.0,
                    1,
                ),
            },
            scan_timestamp=3000.0,
        )
        
        import_graph = ImportGraph()
        descriptors = {
            Path("test.py"): FileDescriptor(
                Path("test.py"), "utility", "Test file", [], [], False, 10
            ),
        }
        
        return CacheEntry(
            repo_path=repo_path,
            repo_map=repo_map,
            import_graph=import_graph,
            descriptors=descriptors,
            cache_time=time.time(),
            file_mtimes={Path("test.py"): 2000.0},
        )
    
    # ------------------------------------------------------------------
    # Test cache storage and retrieval
    # ------------------------------------------------------------------
    
    def test_cache_storage_and_retrieval(
        self,
        cache_manager: CacheManager,
        temp_cache_dir: TemporaryDirectory,
    ):
        """Test storing and retrieving cache entry."""
        # Create real test repo
        repo_path = Path(temp_cache_dir.name) / "test_repo"
        repo_path.mkdir()
        test_file = repo_path / "test.py"
        test_file.write_text("# test file")
        
        # Create entry with correct mtime
        mtime = test_file.stat().st_mtime
        sample_entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path,
                FileNode(Path("."), repo_path, True, 0, 0.0, 0),
                {
                    Path("test.py"): FileNode(
                        Path("test.py"),
                        test_file,
                        False,
                        len("# test file"),
                        mtime,
                        1,
                    )
                },
                time.time(),
            ),
            import_graph=ImportGraph(),
            descriptors={
                Path("test.py"): FileDescriptor(
                    Path("test.py"),
                    "utility",
                    "Test file",
                    [],
                    [],
                    False,
                    1,
                )
            },
            cache_time=time.time(),
            file_mtimes={Path("test.py"): mtime},
        )
        
        # Store entry
        cache_manager.put(sample_entry)
        
        # Retrieve entry
        retrieved = cache_manager.get(repo_path)
        
        assert retrieved is not None
        assert retrieved.repo_path == sample_entry.repo_path
        assert len(retrieved.file_mtimes) == len(sample_entry.file_mtimes)
    
    def test_cache_miss_no_file(self, cache_manager: CacheManager):
        """Test cache miss when no cache file exists."""
        result = cache_manager.get(Path("/nonexistent/repo"))
        assert result is None
    
    def test_cache_disabled(self):
        """Test cache operations when caching is disabled."""
        config = RepoMapperConfig(enable_cache=False)
        manager = CacheManager(config)
        
        # Create sample entry
        repo_path = Path("/test/repo")
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path, FileNode(Path("."), repo_path, True, 0, 0.0, 0), {}, 0.0
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes={},
        )
        
        # Store should be no-op
        manager.put(entry)
        
        # Get should return None
        result = manager.get(repo_path)
        assert result is None
    
    # ------------------------------------------------------------------
    # Test cache invalidation
    # ------------------------------------------------------------------
    
    def test_cache_invalidation(
        self,
        cache_manager: CacheManager,
        temp_cache_dir: TemporaryDirectory,
    ):
        """Test explicit cache invalidation."""
        # Create real test repo
        repo_path = Path(temp_cache_dir.name) / "test_repo"
        repo_path.mkdir()
        test_file = repo_path / "test.py"
        test_file.write_text("# test file")
        
        # Create entry with correct mtime
        mtime = test_file.stat().st_mtime
        sample_entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path,
                FileNode(Path("."), repo_path, True, 0, 0.0, 0),
                {
                    Path("test.py"): FileNode(
                        Path("test.py"),
                        test_file,
                        False,
                        len("# test file"),
                        mtime,
                        1,
                    )
                },
                time.time(),
            ),
            import_graph=ImportGraph(),
            descriptors={
                Path("test.py"): FileDescriptor(
                    Path("test.py"),
                    "utility",
                    "Test file",
                    [],
                    [],
                    False,
                    1,
                )
            },
            cache_time=time.time(),
            file_mtimes={Path("test.py"): mtime},
        )
        
        # Store entry
        cache_manager.put(sample_entry)
        
        # Verify it exists
        assert cache_manager.get(repo_path) is not None
        
        # Invalidate
        cache_manager.invalidate(repo_path)
        
        # Should be gone
        assert cache_manager.get(repo_path) is None
    
    def test_cache_format_version_mismatch(
        self,
        cache_manager: CacheManager,
        sample_entry: CacheEntry,
    ):
        """Test cache invalidation on format version mismatch."""
        # Store entry
        cache_manager.put(sample_entry)
        
        # Manually modify cache file to have wrong version
        cache_path = cache_manager._get_cache_path(sample_entry.repo_path)
        with open(cache_path, "r") as f:
            data = json.load(f)
        data["cache_format_version"] = "0.9"
        with open(cache_path, "w") as f:
            json.dump(data, f)
        
        # Get should return None (invalid version)
        result = cache_manager.get(sample_entry.repo_path)
        assert result is None
    
    def test_cache_corruption_handling(
        self,
        cache_manager: CacheManager,
        sample_entry: CacheEntry,
    ):
        """Test handling of corrupted cache files."""
        # Store entry
        cache_manager.put(sample_entry)
        
        # Corrupt the cache file
        cache_path = cache_manager._get_cache_path(sample_entry.repo_path)
        with open(cache_path, "w") as f:
            f.write("{ invalid json ")
        
        # Get should return None (corrupted)
        result = cache_manager.get(sample_entry.repo_path)
        assert result is None
    
    # ------------------------------------------------------------------
    # Test cache validation (Task 6.3)
    # ------------------------------------------------------------------
    
    def test_cache_validation_valid(
        self,
        cache_manager: CacheManager,
        temp_cache_dir: TemporaryDirectory,
    ):
        """Test cache validation when files haven't changed."""
        # Create real test repo
        repo_path = Path(temp_cache_dir.name) / "test_repo"
        repo_path.mkdir()
        test_file = repo_path / "test.py"
        test_file.write_text("# test file")
        
        # Create entry with correct mtime
        mtime = test_file.stat().st_mtime
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path, FileNode(Path("."), repo_path, True, 0, 0.0, 0), {}, 0.0
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes={Path("test.py"): mtime},
        )
        
        # Validation should pass
        assert cache_manager.is_valid(entry, repo_path)
    
    def test_cache_validation_file_modified(
        self,
        cache_manager: CacheManager,
        temp_cache_dir: TemporaryDirectory,
    ):
        """Test cache invalidation when tracked file is modified."""
        # Create real test repo
        repo_path = Path(temp_cache_dir.name) / "test_repo"
        repo_path.mkdir()
        test_file = repo_path / "test.py"
        test_file.write_text("# test file")
        
        # Create entry with old mtime
        old_mtime = test_file.stat().st_mtime
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path, FileNode(Path("."), repo_path, True, 0, 0.0, 0), {}, 0.0
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes={Path("test.py"): old_mtime},
        )
        
        # Modify file (need to ensure mtime changes)
        time.sleep(0.1)
        test_file.write_text("# modified")
        
        # Validation should fail
        assert not cache_manager.is_valid(entry, repo_path)
    
    def test_cache_validation_file_deleted(
        self,
        cache_manager: CacheManager,
        temp_cache_dir: TemporaryDirectory,
    ):
        """Test cache invalidation when tracked file is deleted."""
        # Create real test repo
        repo_path = Path(temp_cache_dir.name) / "test_repo"
        repo_path.mkdir()
        test_file = repo_path / "test.py"
        test_file.write_text("# test file")
        
        mtime = test_file.stat().st_mtime
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path, FileNode(Path("."), repo_path, True, 0, 0.0, 0), {}, 0.0
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes={Path("test.py"): mtime},
        )
        
        # Delete file
        test_file.unlink()
        
        # Validation should fail
        assert not cache_manager.is_valid(entry, repo_path)
    
    def test_cache_validation_new_python_file(
        self,
        cache_manager: CacheManager,
        temp_cache_dir: TemporaryDirectory,
    ):
        """Test cache invalidation when new Python file added."""
        # Create real test repo
        repo_path = Path(temp_cache_dir.name) / "test_repo"
        repo_path.mkdir()
        test_file = repo_path / "test.py"
        test_file.write_text("# test file")
        
        mtime = test_file.stat().st_mtime
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path, FileNode(Path("."), repo_path, True, 0, 0.0, 0), {}, 0.0
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time(),
            file_mtimes={Path("test.py"): mtime},
        )
        
        # Add new Python file
        new_file = repo_path / "new_file.py"
        new_file.write_text("# new file")
        
        # Validation should fail
        assert not cache_manager.is_valid(entry, repo_path)
    
    def test_cache_validation_ttl_expired(
        self,
        temp_cache_dir: TemporaryDirectory,
    ):
        """Test cache invalidation when TTL expires."""
        # Config with very short TTL
        config = RepoMapperConfig(
            enable_cache=True,
            cache_dir=Path(temp_cache_dir.name),
            cache_ttl_seconds=0.1,  # 100ms TTL
        )
        manager = CacheManager(config)
        
        # Create entry with old cache time
        repo_path = Path(temp_cache_dir.name) / "test_repo"
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=RepositoryMap(
                repo_path, FileNode(Path("."), repo_path, True, 0, 0.0, 0), {}, 0.0
            ),
            import_graph=ImportGraph(),
            descriptors={},
            cache_time=time.time() - 1.0,  # 1 second old
            file_mtimes={},
        )
        
        # Validation should fail (TTL expired)
        assert not manager.is_valid(entry, repo_path)
    
    # ------------------------------------------------------------------
    # Test cache path generation
    # ------------------------------------------------------------------
    
    def test_cache_path_generation(self, cache_manager: CacheManager):
        """Test that cache paths are generated consistently."""
        repo1 = Path("/test/repo1")
        repo2 = Path("/test/repo2")
        
        path1_a = cache_manager._get_cache_path(repo1)
        path1_b = cache_manager._get_cache_path(repo1)
        path2 = cache_manager._get_cache_path(repo2)
        
        # Same repo should generate same path
        assert path1_a == path1_b
        
        # Different repos should generate different paths
        assert path1_a != path2
        
        # Paths should be in cache directory
        assert path1_a.parent == cache_manager.cache_dir
        assert path2.parent == cache_manager.cache_dir
