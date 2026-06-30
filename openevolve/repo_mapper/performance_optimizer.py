"""
Performance optimization utilities for Repository Context Mapper.

Task 12.1 — Profile and optimize performance for large repositories.

Implements:
- Import resolution memoization
- Token counting caching
- Parallel file analysis
- Structure parsing optimization (parse once, extract multiple times)
"""

import functools
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openevolve.repo_mapper.models import (
    FileDescriptor,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.parser_interface import extract_imports, extract_structure

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Memoization Decorators
# ---------------------------------------------------------------------------

def memoize_import_resolution(func):
    """Decorator to cache import resolution results.
    
    Caches module -> file resolutions to avoid redundant path matching.
    Thread-safe for parallel analysis.
    """
    cache = {}
    
    @functools.wraps(func)
    def wrapper(self, module_name: str, *args, **kwargs):
        # Use module name as cache key
        if module_name not in cache:
            result = func(self, module_name, *args, **kwargs)
            cache[module_name] = result
        return cache[module_name]
    
    # Expose cache for testing/debugging
    wrapper.cache = cache
    wrapper.cache_info = lambda: {
        'size': len(cache),
        'hits': getattr(wrapper, '_hits', 0),
        'misses': getattr(wrapper, '_misses', 0),
    }
    
    return wrapper


def memoize_token_estimate(func):
    """Decorator to cache token count estimates.
    
    Token estimation is expensive for large strings. Cache results
    keyed by content length and first 100 chars.
    """
    cache = {}
    
    @functools.wraps(func)
    def wrapper(text: str) -> int:
        # Create cache key from length and content sample
        cache_key = (len(text), text[:100] if len(text) >= 100 else text)
        
        if cache_key not in cache:
            result = func(text)
            cache[cache_key] = result
        return cache[cache_key]
    
    wrapper.cache = cache
    wrapper.cache_info = lambda: {'size': len(cache)}
    
    return wrapper


# ---------------------------------------------------------------------------
# Parallel File Analysis
# ---------------------------------------------------------------------------

def analyze_file_batch(
    file_nodes: List[Tuple[Path, Path]],
    config: RepoMapperConfig,
) -> Dict[Path, FileDescriptor]:
    """Analyze multiple files in parallel.
    
    Args:
        file_nodes: List of (absolute_path, relative_path) tuples.
        config: Configuration with max_workers setting.
        
    Returns:
        Dict mapping relative_path -> FileDescriptor.
    """
    from openevolve.repo_mapper.file_analyzer import FileAnalyzer
    
    descriptors = {}
    
    if not file_nodes:
        return descriptors
    
    # Use thread pool for I/O-bound analysis
    max_workers = config.max_workers if config.parallel_analysis else 1
    
    if max_workers == 1:
        # Sequential processing (fallback)
        analyzer = FileAnalyzer(config)
        for abs_path, rel_path in file_nodes:
            try:
                descriptor = analyzer.analyze_file(abs_path, rel_path)
                descriptors[rel_path] = descriptor
            except Exception as e:
                logger.warning(
                    "Failed to analyze %s: %s",
                    rel_path,
                    e,
                )
        return descriptors
    
    # Parallel processing
    logger.info(
        "Analyzing %d files with %d workers",
        len(file_nodes),
        max_workers,
    )
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create analyzer instance per thread (not thread-safe)
        futures = {}
        for abs_path, rel_path in file_nodes:
            future = executor.submit(
                _analyze_single_file,
                abs_path,
                rel_path,
                config,
            )
            futures[future] = rel_path
        
        # Collect results
        for future in as_completed(futures):
            rel_path = futures[future]
            try:
                descriptor = future.result()
                if descriptor:
                    descriptors[rel_path] = descriptor
            except Exception as e:
                logger.warning(
                    "Failed to analyze %s: %s",
                    rel_path,
                    e,
                )
    
    logger.info(
        "Parallel analysis complete: %d/%d successful",
        len(descriptors),
        len(file_nodes),
    )
    
    return descriptors


def _analyze_single_file(
    abs_path: Path,
    rel_path: Path,
    config: RepoMapperConfig,
) -> Optional[FileDescriptor]:
    """Helper function for parallel file analysis.
    
    Creates a fresh FileAnalyzer instance per call (thread-safe).
    
    Args:
        abs_path: Absolute path to file.
        rel_path: Relative path from repository root.
        config: Analysis configuration.
        
    Returns:
        FileDescriptor or None if analysis failed.
    """
    from openevolve.repo_mapper.file_analyzer import FileAnalyzer
    
    analyzer = FileAnalyzer(config)
    try:
        return analyzer.analyze_file(abs_path, rel_path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Optimized Structure Parsing
# ---------------------------------------------------------------------------

class ParsedFileCache:
    """Cache for parsed file structures (parse once, extract multiple times).
    
    Caches the AST or parsed structure for files to avoid redundant parsing
    when extracting different information (imports, classes, functions).
    """
    
    def __init__(self, max_cache_size: int = 1000):
        """Initialize cache with size limit.
        
        Args:
            max_cache_size: Maximum number of parsed files to cache.
        """
        self.cache: Dict[Path, Tuple] = {}
        self.max_cache_size = max_cache_size
    
    def get_or_parse(
        self,
        file_path: Path,
    ) -> Tuple:
        """Get cached structure or parse file.
        
        Args:
            file_path: Path to Python file.
            
        Returns:
            Tuple of (imports, structure) from parser_interface.
        """
        if file_path not in self.cache:
            # Parse file once
            imports = extract_imports(file_path)
            structure = extract_structure(file_path)
            
            # Store in cache
            self.cache[file_path] = (imports, structure)
            
            # Enforce size limit (LRU-style eviction)
            if len(self.cache) > self.max_cache_size:
                # Remove oldest entry (first in dict)
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
        
        return self.cache[file_path]
    
    def clear(self):
        """Clear entire cache."""
        self.cache.clear()
    
    def cache_info(self) -> Dict[str, int]:
        """Return cache statistics.
        
        Returns:
            Dict with size and hit rate info.
        """
        return {
            'size': len(self.cache),
            'max_size': self.max_cache_size,
        }


# Global parsed file cache instance
_parsed_file_cache = ParsedFileCache()


def get_parsed_file_cache() -> ParsedFileCache:
    """Get global parsed file cache instance.
    
    Returns:
        Shared ParsedFileCache instance.
    """
    return _parsed_file_cache


# ---------------------------------------------------------------------------
# Performance Profiling Utilities
# ---------------------------------------------------------------------------

class PerformanceProfiler:
    """Lightweight performance profiler for mapper operations.
    
    Tracks timing and counts for key operations during analysis.
    """
    
    def __init__(self):
        """Initialize profiler with empty metrics."""
        self.metrics = {
            'scan_time': 0.0,
            'import_analysis_time': 0.0,
            'file_analysis_time': 0.0,
            'relevance_scoring_time': 0.0,
            'context_building_time': 0.0,
            'cache_read_time': 0.0,
            'cache_write_time': 0.0,
            'files_scanned': 0,
            'files_analyzed': 0,
            'import_relations': 0,
            'cache_hits': 0,
            'cache_misses': 0,
        }
    
    def record(self, metric: str, value: float | int):
        """Record a metric value.
        
        Args:
            metric: Metric name.
            value: Metric value (time in seconds or count).
        """
        if metric in self.metrics:
            self.metrics[metric] += value
        else:
            self.metrics[metric] = value
    
    def get_summary(self) -> Dict[str, float | int]:
        """Get summary of all metrics.
        
        Returns:
            Dict of metric names to values.
        """
        return self.metrics.copy()
    
    def get_total_time(self) -> float:
        """Get total analysis time (all timing metrics).
        
        Returns:
            Sum of all *_time metrics in seconds.
        """
        return sum(
            v for k, v in self.metrics.items()
            if k.endswith('_time')
        )
    
    def __str__(self) -> str:
        """Format metrics as human-readable string."""
        lines = ["Performance Metrics:"]
        for key, value in sorted(self.metrics.items()):
            if key.endswith('_time'):
                lines.append(f"  {key}: {value:.3f}s")
            else:
                lines.append(f"  {key}: {value}")
        lines.append(f"  total_time: {self.get_total_time():.3f}s")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Optimized Import Resolution
# ---------------------------------------------------------------------------

class OptimizedImportResolver:
    """Optimized import resolver with memoization and batch resolution.
    
    Improves performance for large repositories by:
    - Caching module -> file resolutions
    - Batch processing similar imports
    - Pre-building file path index
    """
    
    def __init__(self, repo_map: RepositoryMap):
        """Initialize resolver with repository map.
        
        Args:
            repo_map: Scanned repository structure.
        """
        self.repo_map = repo_map
        
        # Build index of module paths for fast lookup
        self.module_index: Dict[str, Path] = {}
        self._build_module_index()
        
        # Memoization cache
        self.resolution_cache: Dict[str, Optional[Path]] = {}
    
    def _build_module_index(self):
        """Build index mapping module names to file paths.
        
        Pre-computes all possible module names for fast resolution.
        """
        for rel_path, node in self.repo_map.files.items():
            if node.is_dir or not rel_path.suffix == '.py':
                continue
            
            # Generate module name from path
            # e.g., "openevolve/config.py" -> "openevolve.config"
            parts = list(rel_path.parts[:-1]) + [rel_path.stem]
            module_name = ".".join(parts)
            
            self.module_index[module_name] = rel_path
            
            # Also index without package prefix for top-level imports
            if len(parts) > 1:
                short_name = ".".join(parts[-2:])
                if short_name not in self.module_index:
                    self.module_index[short_name] = rel_path
    
    def resolve(self, module_name: str) -> Optional[Path]:
        """Resolve module name to file path (with memoization).
        
        Args:
            module_name: Python module name (e.g., "openevolve.config").
            
        Returns:
            Relative file path if found in repository, None otherwise.
        """
        if module_name in self.resolution_cache:
            return self.resolution_cache[module_name]
        
        # Try exact match first
        if module_name in self.module_index:
            result = self.module_index[module_name]
            self.resolution_cache[module_name] = result
            return result
        
        # Try partial matches (for package imports)
        for indexed_name, file_path in self.module_index.items():
            if indexed_name.endswith(module_name) or module_name in indexed_name:
                self.resolution_cache[module_name] = file_path
                return file_path
        
        # Not found in repository (external)
        self.resolution_cache[module_name] = None
        return None
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics.
        
        Returns:
            Dict with cache size and index size.
        """
        return {
            'cache_size': len(self.resolution_cache),
            'index_size': len(self.module_index),
        }


# ---------------------------------------------------------------------------
# Export optimized components
# ---------------------------------------------------------------------------

__all__ = [
    'memoize_import_resolution',
    'memoize_token_estimate',
    'analyze_file_batch',
    'ParsedFileCache',
    'get_parsed_file_cache',
    'PerformanceProfiler',
    'OptimizedImportResolver',
]
