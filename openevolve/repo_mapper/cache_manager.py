"""
CacheManager: Cache repository analysis for performance optimization.

Task 6.2 — Performance optimization through caching.
Implements incremental invalidation based on file modification times.

NOTE: This phase is OPTIONAL FOR MVP but provides significant performance gains.

Implements Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.7
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional

from openevolve.repo_mapper.models import (
    FileDescriptor,
    ImportGraph,
    ImportRelation,
    RepoMapperConfig,
    RepositoryMap,
    FileNode,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task 6.1 — CacheEntry Data Model
# ---------------------------------------------------------------------------

class CacheEntry:
    """Cached repository analysis with metadata for validation.
    
    Stores complete analysis results (repository map, import graph, descriptors)
    along with file modification times for incremental invalidation.
    
    Attributes:
        repo_path: Absolute path to repository root.
        repo_map: Scanned repository structure.
        import_graph: Dependency graph.
        descriptors: File summaries keyed by relative path.
        cache_time: Unix timestamp when cache was created.
        file_mtimes: File modification times for validation.
        cache_format_version: Version string for compatibility checking.
    """
    
    CACHE_FORMAT_VERSION = "1.0"
    
    def __init__(
        self,
        repo_path: Path,
        repo_map: RepositoryMap,
        import_graph: ImportGraph,
        descriptors: Dict[Path, FileDescriptor],
        cache_time: float,
        file_mtimes: Dict[Path, float],
    ):
        """Initialize cache entry.
        
        Args:
            repo_path: Absolute path to repository root.
            repo_map: Repository structure from scanner.
            import_graph: Import dependencies from analyzer.
            descriptors: File summaries from file analyzer.
            cache_time: Unix timestamp of cache creation.
            file_mtimes: Map of relative_path -> modification_time for tracked files.
        """
        self.repo_path = repo_path
        self.repo_map = repo_map
        self.import_graph = import_graph
        self.descriptors = descriptors
        self.cache_time = cache_time
        self.file_mtimes = file_mtimes
        self.cache_format_version = self.CACHE_FORMAT_VERSION
    
    def to_dict(self) -> dict:
        """Serialize cache entry to dictionary for JSON storage.
        
        Returns:
            Dictionary representation suitable for JSON serialization.
        """
        return {
            "cache_format_version": self.cache_format_version,
            "repo_path": self.repo_path.as_posix(),  # Use POSIX paths for cross-platform
            "cache_time": self.cache_time,
            "file_mtimes": {Path(k).as_posix(): v for k, v in self.file_mtimes.items()},
            "repo_map": {
                "repo_path": self.repo_map.repo_path.as_posix(),
                "scan_timestamp": self.repo_map.scan_timestamp,
                "files": {
                    Path(k).as_posix(): {
                        "path": v.path.as_posix(),
                        "absolute_path": v.absolute_path.as_posix(),
                        "is_dir": v.is_dir,
                        "size_bytes": v.size_bytes,
                        "modified_time": v.modified_time,
                        "depth": v.depth,
                    }
                    for k, v in self.repo_map.files.items()
                },
            },
            "import_graph": {
                "relations": [
                    {
                        "source_file": r.source_file.as_posix(),
                        "target_module": r.target_module,
                        "target_file": r.target_file.as_posix() if r.target_file else None,
                        "import_type": r.import_type,
                        "line_number": r.line_number,
                    }
                    for r in self.import_graph.relations
                ],
            },
            "descriptors": {
                Path(k).as_posix(): {
                    "file_path": v.file_path.as_posix(),
                    "role": v.role,
                    "summary": v.summary,
                    "classes": v.classes,
                    "functions": v.functions,
                    "has_main": v.has_main,
                    "loc": v.loc,
                }
                for k, v in self.descriptors.items()
            },
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CacheEntry":
        """Deserialize cache entry from dictionary.
        
        Args:
            data: Dictionary from JSON.
            
        Returns:
            Reconstructed CacheEntry.
            
        Raises:
            KeyError: If required fields missing.
            ValueError: If data malformed.
        """
        # Reconstruct RepositoryMap
        repo_path = Path(data["repo_map"]["repo_path"])
        files = {}
        for path_str, node_data in data["repo_map"]["files"].items():
            files[Path(path_str)] = FileNode(
                path=Path(node_data["path"]),
                absolute_path=Path(node_data["absolute_path"]),
                is_dir=node_data["is_dir"],
                size_bytes=node_data["size_bytes"],
                modified_time=node_data["modified_time"],
                depth=node_data["depth"],
            )
        
        # Get root node - either from files or create a dummy one
        root_node = files.get(Path("."))
        if root_node is None and files:
            # Use first file as fallback
            root_node = files[list(files.keys())[0]]
        elif root_node is None:
            # Create dummy root for empty repos
            root_node = FileNode(Path("."), repo_path, True, 0, 0.0, 0)
        
        repo_map = RepositoryMap(
            repo_path=repo_path,
            root_node=root_node,
            files=files,
            scan_timestamp=data["repo_map"]["scan_timestamp"],
        )
        
        # Reconstruct ImportGraph
        import_graph = ImportGraph()
        for rel_data in data["import_graph"]["relations"]:
            relation = ImportRelation(
                source_file=Path(rel_data["source_file"]),
                target_module=rel_data["target_module"],
                target_file=Path(rel_data["target_file"]) if rel_data["target_file"] else None,
                import_type=rel_data["import_type"],
                line_number=rel_data["line_number"],
            )
            import_graph.add_relation(relation)
        
        # Reconstruct descriptors
        descriptors = {}
        for path_str, desc_data in data["descriptors"].items():
            descriptors[Path(path_str)] = FileDescriptor(
                file_path=Path(desc_data["file_path"]),
                role=desc_data["role"],
                summary=desc_data["summary"],
                classes=desc_data["classes"],
                functions=desc_data["functions"],
                has_main=desc_data["has_main"],
                loc=desc_data["loc"],
            )
        
        # Reconstruct file_mtimes
        file_mtimes = {Path(k): v for k, v in data["file_mtimes"].items()}
        
        return cls(
            repo_path=Path(data["repo_path"]),
            repo_map=repo_map,
            import_graph=import_graph,
            descriptors=descriptors,
            cache_time=data["cache_time"],
            file_mtimes=file_mtimes,
        )


# ---------------------------------------------------------------------------
# Task 6.2 — CacheManager Class
# ---------------------------------------------------------------------------

class CacheManager:
    """Manages repository analysis cache with incremental invalidation.
    
    Strategy:
    - Cache keyed by repository path hash
    - Validate by checking file modification times
    - Invalidate conservatively (on any file change or new Python files)
    - Store as JSON for human readability and debugging
    
    Attributes:
        config: RepoMapperConfig controlling cache behavior.
        cache_dir: Directory where cache files are stored.
    """
    
    def __init__(self, config: RepoMapperConfig):
        """Initialize cache manager.
        
        Args:
            config: RepoMapperConfig with cache settings.
        """
        self.config = config
        
        # Determine cache directory
        if config.cache_dir:
            self.cache_dir = Path(config.cache_dir)
        else:
            # Default to .cache/repo-context in repo root
            self.cache_dir = Path.cwd() / ".cache" / "repo-context"
        
        # Create cache directory if it doesn't exist
        if config.enable_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Cache directory: {self.cache_dir}")
    
    # ------------------------------------------------------------------
    # Public API (Task 6.2)
    # ------------------------------------------------------------------
    
    def get(self, repo_path: Path) -> Optional[CacheEntry]:
        """Retrieve cached entry if valid.
        
        Args:
            repo_path: Absolute path to repository root.
            
        Returns:
            CacheEntry if cache exists and is valid, None otherwise.
        """
        if not self.config.enable_cache:
            return None
        
        cache_path = self._get_cache_path(repo_path)
        
        if not cache_path.exists():
            logger.debug(f"Cache miss: {repo_path} (no cache file)")
            return None
        
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Validate cache format version
            if data.get("cache_format_version") != CacheEntry.CACHE_FORMAT_VERSION:
                logger.info(
                    f"Cache format version mismatch for {repo_path}. "
                    "Invalidating cache."
                )
                self.invalidate(repo_path)
                return None
            
            # Deserialize entry
            entry = CacheEntry.from_dict(data)
            
            # Validate cache is still fresh
            if not self.is_valid(entry, repo_path):
                logger.debug(f"Cache invalid: {repo_path} (files changed)")
                self.invalidate(repo_path)
                return None
            
            logger.info(f"Cache hit: {repo_path}")
            return entry
            
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"Cache corrupted for {repo_path}: {e}. Regenerating.")
            self.invalidate(repo_path)
            return None
        except Exception as e:
            logger.error(f"Unexpected error reading cache for {repo_path}: {e}")
            return None
    
    def put(self, entry: CacheEntry) -> None:
        """Store cache entry to disk.
        
        Args:
            entry: CacheEntry to store.
        """
        if not self.config.enable_cache:
            return
        
        cache_path = self._get_cache_path(entry.repo_path)
        
        try:
            # Serialize to JSON with indentation for readability
            data = entry.to_dict()
            
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"Cache stored: {entry.repo_path} -> {cache_path}")
            
        except Exception as e:
            logger.error(f"Failed to write cache for {entry.repo_path}: {e}")
    
    def invalidate(self, repo_path: Path) -> None:
        """Remove cache entry for repository.
        
        Args:
            repo_path: Absolute path to repository root.
        """
        if not self.config.enable_cache:
            return
        
        cache_path = self._get_cache_path(repo_path)
        
        if cache_path.exists():
            try:
                cache_path.unlink()
                logger.info(f"Cache invalidated: {repo_path}")
            except Exception as e:
                logger.warning(f"Failed to invalidate cache for {repo_path}: {e}")
    
    # ------------------------------------------------------------------
    # Task 6.3 — Cache Validation Logic
    # ------------------------------------------------------------------
    
    def is_valid(self, entry: CacheEntry, repo_path: Path) -> bool:
        """Check if cache entry is still valid.
        
        Valid if:
        - All tracked files still exist
        - No tracked file has been modified (mtime changed)
        - No new Python files in tracked directories (recursive)
        - Cache not older than TTL
        
        Args:
            entry: CacheEntry to validate.
            repo_path: Absolute path to repository root.
            
        Returns:
            True if cache is valid, False otherwise.
        """
        # If repo doesn't exist, cache is invalid
        if not repo_path.exists():
            logger.debug(f"Cache invalid: repository path does not exist: {repo_path}")
            return False
        
        # Check cache TTL
        if self.config.cache_ttl_seconds > 0:
            age = time.time() - entry.cache_time
            if age > self.config.cache_ttl_seconds:
                logger.debug(
                    f"Cache expired: age={age:.1f}s > ttl={self.config.cache_ttl_seconds}s"
                )
                return False
        
        # Check all tracked files still exist and haven't been modified
        for rel_path, cached_mtime in entry.file_mtimes.items():
            abs_path = repo_path / rel_path
            
            # File must exist
            if not abs_path.exists():
                logger.debug(f"Cache invalid: tracked file deleted: {rel_path}")
                return False
            
            # File mtime must match
            try:
                current_mtime = abs_path.stat().st_mtime
                if abs(current_mtime - cached_mtime) > 0.01:  # Allow small float errors
                    logger.debug(
                        f"Cache invalid: file modified: {rel_path} "
                        f"(mtime changed from {cached_mtime} to {current_mtime})"
                    )
                    return False
            except OSError as e:
                logger.debug(f"Cache invalid: cannot stat {rel_path}: {e}")
                return False
        
        # Check for new Python files in the repository (recursive scan)
        # Get all tracked Python files
        tracked_py_files = {Path(rel_path) for rel_path in entry.file_mtimes.keys()}
        
        # Scan for all current Python files in repo
        try:
            current_py_files = set()
            for py_file in repo_path.rglob("*.py"):
                try:
                    rel_py = py_file.relative_to(repo_path)
                    # Skip files in ignored directories
                    if self._should_ignore_for_cache(rel_py):
                        continue
                    current_py_files.add(rel_py)
                except ValueError:
                    # File is not relative to repo_path (shouldn't happen with rglob)
                    continue
            
            # Check if any new Python files exist
            new_files = current_py_files - tracked_py_files
            if new_files:
                logger.debug(f"Cache invalid: {len(new_files)} new Python file(s) detected: {list(new_files)[:3]}")
                return False
                
        except Exception as e:
            logger.debug(f"Cache invalid: error scanning for new files: {e}")
            return False
        
        # All checks passed
        return True
    
    def _should_ignore_for_cache(self, rel_path: Path) -> bool:
        """Check if a path should be ignored during cache validation.
        
        Args:
            rel_path: Path relative to repo root.
            
        Returns:
            True if path should be ignored.
        """
        # Common patterns to ignore during cache validation
        ignore_patterns = [
            ".git", ".hg", ".svn",
            "__pycache__", ".pytest_cache", ".mypy_cache",
            ".venv", "venv", "env",
            "node_modules", "bower_components",
            "dist", "build", ".egg-info",
            ".idea", ".vscode",
        ]
        
        # Check if any parent directory matches ignore patterns
        for part in rel_path.parts:
            if any(part.startswith(pattern) or part.endswith(pattern) 
                   for pattern in ignore_patterns):
                return True
        
        return False
    
    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    
    def _get_cache_path(self, repo_path: Path) -> Path:
        """Generate cache file path from repository path hash.
        
        Args:
            repo_path: Absolute path to repository root.
            
        Returns:
            Path to cache file.
        """
        # Hash repo path for stable cache key
        repo_hash = hashlib.sha256(str(repo_path).encode()).hexdigest()[:16]
        return self.cache_dir / f"repo_{repo_hash}.json"
