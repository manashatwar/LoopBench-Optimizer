"""
RepoContextMapper: Main orchestrator for repository context generation.

Task 7.1 — Coordinates all subcomponents (scanner, analyzer, scorer, builder)
and manages the complete analysis pipeline with error handling and caching.

Implements Requirements: All requirements 1-10
"""

import logging
import time
from pathlib import Path
from typing import Dict

from openevolve.repo_mapper.cache_manager import CacheEntry, CacheManager
from openevolve.repo_mapper.context_builder import ContextBuilder
from openevolve.repo_mapper.file_analyzer import FileAnalyzer
from openevolve.repo_mapper.import_analyzer import ImportAnalyzer
from openevolve.repo_mapper.models import (
    ContextMap,
    FileDescriptor,
    ImportGraph,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.performance_optimizer import (
    PerformanceProfiler,
    analyze_file_batch,
)
from openevolve.repo_mapper.relevance_scorer import RelevanceScorer
from openevolve.repo_mapper.scanner import RepositoryScanner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task 7.2 — Error Handling Infrastructure
# ---------------------------------------------------------------------------

class RepoMapperError(Exception):
    """Base exception for repository mapper errors.
    
    Attributes:
        error_code: Machine-readable error code for categorization.
        message: Human-readable error description.
    """
    
    def __init__(self, message: str, error_code: str):
        """Initialize error with message and code.
        
        Args:
            message: Human-readable error description.
            error_code: Machine-readable error code (e.g., "REPO_NOT_FOUND").
        """
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class RepositoryScanError(RepoMapperError):
    """Errors during repository scanning.
    
    Examples:
    - Repository path does not exist
    - Permission denied during traversal
    - File system errors
    """
    pass


class ImportAnalysisError(RepoMapperError):
    """Errors during import analysis.
    
    Examples:
    - Import parsing failures
    - Circular import issues
    - Graph construction errors
    """
    pass


class CacheError(RepoMapperError):
    """Errors related to cache operations.
    
    Examples:
    - Cache corruption
    - Cache write failures
    - Invalid cache format
    """
    pass


class ContextBuildError(RepoMapperError):
    """Errors during context map building.
    
    Examples:
    - Token budget overflow (unrecoverable)
    - Missing required descriptors
    - Invalid target file
    """
    pass


# ---------------------------------------------------------------------------
# Task 7.1 — RepoContextMapper Orchestrator
# ---------------------------------------------------------------------------

class RepoContextMapper:
    """Main orchestrator for repository context generation.
    
    Coordinates all subcomponents to produce context maps for LLM prompts:
    1. Repository scanning (with caching)
    2. Import analysis
    3. File analysis and descriptor generation
    4. Relevance scoring
    5. Context map building within token budgets
    
    Attributes:
        config: RepoMapperConfig controlling all behavior.
        cache_manager: Handles cache storage/retrieval.
        scanner: Scans repository file tree.
        import_analyzer: Builds import dependency graph.
        file_analyzer: Generates file descriptors.
        relevance_scorer: Scores files by relevance to target.
        context_builder: Builds token-budget-aware context maps.
    
    Example:
        >>> config = RepoMapperConfig(token_budget=2000)
        >>> mapper = RepoContextMapper(config)
        >>> context = mapper.get_context_map(
        ...     repo_path=Path("/path/to/repo"),
        ...     target_file=Path("/path/to/repo/src/main.py"),
        ... )
        >>> print(context.to_prompt_section())
    """
    
    def __init__(self, config: RepoMapperConfig):
        """Initialize orchestrator with configuration.
        
        Args:
            config: RepoMapperConfig controlling scanning, analysis,
                token budgets, caching, and performance.
        """
        self.config = config
        
        # Initialize all subcomponents
        self.cache_manager = CacheManager(config)
        self.scanner = RepositoryScanner(config)
        self.import_analyzer = ImportAnalyzer(config)
        self.file_analyzer = FileAnalyzer(config)
        self.relevance_scorer = RelevanceScorer(config)
        self.context_builder = ContextBuilder(config)
        
        # Performance profiler (optional)
        self.profiler = PerformanceProfiler() if logger.isEnabledFor(logging.DEBUG) else None
        
        logger.info(
            "RepoContextMapper initialized: cache=%s, token_budget=%d, parallel=%s",
            config.enable_cache,
            config.token_budget,
            config.parallel_analysis,
        )
    
    # ------------------------------------------------------------------
    # Public API (Task 7.1)
    # ------------------------------------------------------------------
    
    def get_context_map(
        self,
        repo_path: Path,
        target_file: Path,
    ) -> ContextMap:
        """Generate context map for a target file.
        
        Complete analysis pipeline:
        1. Check cache (if enabled)
        2. Scan repository (if cache miss)
        3. Analyze imports
        4. Generate file descriptors
        5. Score files by relevance
        6. Build context map within token budget
        7. Store cache (if enabled)
        
        Args:
            repo_path: Absolute path to repository root.
            target_file: Absolute path to target file being optimized.
            
        Returns:
            ContextMap ready for LLM prompt insertion.
            
        Raises:
            RepositoryScanError: If repository cannot be scanned.
            ImportAnalysisError: If import analysis fails critically.
            ContextBuildError: If context map cannot be built.
        """
        # Normalize paths
        repo_path = repo_path.resolve()
        target_file = target_file.resolve()
        
        # Convert target to relative path
        try:
            target_rel = target_file.relative_to(repo_path)
        except ValueError:
            raise ContextBuildError(
                f"Target file {target_file} is not within repository {repo_path}",
                error_code="TARGET_OUTSIDE_REPO",
            )
        
        logger.info(
            "Building context map: repo=%s, target=%s",
            repo_path,
            target_rel,
        )
        
        start_time = time.time()
        
        # Step 1: Try cache
        cached_entry = None
        if self.config.enable_cache:
            try:
                cached_entry = self.cache_manager.get(repo_path)
                if cached_entry:
                    logger.info("Cache hit for %s", repo_path)
            except Exception as e:
                logger.warning("Cache retrieval failed: %s. Continuing without cache.", e)
        
        # Step 2-4: Full analysis or use cache
        if cached_entry:
            repo_map = cached_entry.repo_map
            import_graph = cached_entry.import_graph
            descriptors = cached_entry.descriptors
        else:
            # Perform full analysis
            repo_map, import_graph, descriptors = self._perform_full_analysis(
                repo_path, target_rel
            )
            
            # Store in cache
            if self.config.enable_cache:
                try:
                    self._store_in_cache(repo_path, repo_map, import_graph, descriptors)
                except Exception as e:
                    logger.warning("Failed to store cache: %s", e)
        
        # Step 5: Score files by relevance
        try:
            scored_files = self.relevance_scorer.score_files(
                target_file=target_rel,
                repo_map=repo_map,
                import_graph=import_graph,
                descriptors=descriptors,
            )
        except Exception as e:
            logger.error("Relevance scoring failed: %s", e)
            # Continue with empty scores (graceful degradation)
            scored_files = []
        
        # Step 6: Build context map
        try:
            context_map = self.context_builder.build(
                target_file=target_rel,
                repo_map=repo_map,
                scored_files=scored_files,
                descriptors=descriptors,
            )
        except ValueError as e:
            raise ContextBuildError(str(e), error_code="BUILD_FAILED")
        except Exception as e:
            logger.error("Context building failed: %s", e)
            raise ContextBuildError(
                f"Failed to build context map: {e}",
                error_code="BUILD_ERROR",
            )
        
        elapsed = time.time() - start_time
        logger.info(
            "Context map built in %.2fs: %d relevant files, %d tokens",
            elapsed,
            len(context_map.relevant_files),
            context_map.token_count,
        )
        
        return context_map
    
    def get_repository_map(self, repo_path: Path) -> RepositoryMap:
        """Generate complete repository map (cached if enabled).
        
        Scans repository and returns the file tree structure.
        Uses cache if available and valid.
        
        Args:
            repo_path: Absolute path to repository root.
            
        Returns:
            RepositoryMap with complete file tree.
            
        Raises:
            RepositoryScanError: If repository cannot be scanned.
        """
        repo_path = repo_path.resolve()
        
        # Try cache first
        if self.config.enable_cache:
            try:
                cached_entry = self.cache_manager.get(repo_path)
                if cached_entry:
                    logger.info("Returning cached repository map for %s", repo_path)
                    return cached_entry.repo_map
            except Exception as e:
                logger.warning("Cache retrieval failed: %s", e)
        
        # Scan repository
        try:
            repo_map = self.scanner.scan(repo_path)
            logger.info(
                "Scanned repository %s: %d files",
                repo_path,
                len(repo_map.files),
            )
            return repo_map
        except FileNotFoundError:
            raise RepositoryScanError(
                f"Repository path does not exist: {repo_path}",
                error_code="REPO_NOT_FOUND",
            )
        except PermissionError as e:
            raise RepositoryScanError(
                f"Permission denied accessing repository: {e}",
                error_code="PERMISSION_DENIED",
            )
        except Exception as e:
            logger.error("Repository scan failed: %s", e)
            raise RepositoryScanError(
                f"Failed to scan repository: {e}",
                error_code="SCAN_FAILED",
            )
    
    # ------------------------------------------------------------------
    # Private: Full analysis pipeline
    # ------------------------------------------------------------------
    
    def _perform_full_analysis(
        self,
        repo_path: Path,
        target_rel: Path,
    ) -> tuple[RepositoryMap, ImportGraph, Dict[Path, FileDescriptor]]:
        """Perform complete repository analysis (scan + imports + descriptors).
        
        Args:
            repo_path: Absolute path to repository root.
            target_rel: Relative path to target file.
            
        Returns:
            Tuple of (repo_map, import_graph, descriptors).
            
        Raises:
            RepositoryScanError: If scanning fails.
            ImportAnalysisError: If import analysis fails critically.
        """
        # Step 1: Scan repository
        scan_start = time.time()
        try:
            repo_map = self.scanner.scan(repo_path)
            if self.profiler:
                self.profiler.record('scan_time', time.time() - scan_start)
                self.profiler.record('files_scanned', len(repo_map.files))
            logger.info("Scanned %d files", len(repo_map.files))
        except (FileNotFoundError, ValueError):
            # ValueError is raised by scanner for nonexistent paths
            raise RepositoryScanError(
                f"Repository path does not exist: {repo_path}",
                error_code="REPO_NOT_FOUND",
            )
        except Exception as e:
            logger.error("Repository scan failed: %s", e)
            raise RepositoryScanError(
                f"Failed to scan repository: {e}",
                error_code="SCAN_FAILED",
            )
        
        # Step 2: Analyze imports (graceful degradation on failure)
        import_start = time.time()
        try:
            import_graph = self.import_analyzer.analyze(repo_map, target_rel)
            if self.profiler:
                self.profiler.record('import_analysis_time', time.time() - import_start)
                self.profiler.record('import_relations', len(import_graph.relations))
            logger.info("Analyzed %d import relations", len(import_graph.relations))
        except Exception as e:
            logger.warning(
                "Import analysis failed: %s. Continuing with empty graph.",
                e,
            )
            # Use empty graph (graceful degradation)
            import_graph = ImportGraph()
        
        # Step 3: Generate file descriptors (optimized with parallel processing)
        file_analysis_start = time.time()
        python_files = [
            node for node in repo_map.files.values()
            if not node.is_dir and node.path.suffix == ".py"
        ]
        
        # Use parallel batch analysis if enabled
        if self.config.parallel_analysis and len(python_files) > 10:
            logger.info("Using parallel file analysis with %d workers", self.config.max_workers)
            file_nodes = [(node.absolute_path, node.path) for node in python_files]
            descriptors = analyze_file_batch(file_nodes, self.config)
        else:
            # Sequential processing for small repositories
            descriptors = {}
            for node in python_files:
                try:
                    descriptor = self.file_analyzer.analyze_file(
                        absolute_path=node.absolute_path,
                        relative_path=node.path,
                    )
                    descriptors[node.path] = descriptor
                except Exception as e:
                    logger.warning(
                        "Failed to analyze file %s: %s. Skipping.",
                        node.path,
                        e,
                    )
                    # Continue with other files (graceful degradation)
        
        if self.profiler:
            self.profiler.record('file_analysis_time', time.time() - file_analysis_start)
            self.profiler.record('files_analyzed', len(descriptors))
        
        logger.info("Generated descriptors for %d files", len(descriptors))
        
        return repo_map, import_graph, descriptors
    
    def _store_in_cache(
        self,
        repo_path: Path,
        repo_map: RepositoryMap,
        import_graph: ImportGraph,
        descriptors: Dict[Path, FileDescriptor],
    ) -> None:
        """Store analysis results in cache.
        
        Args:
            repo_path: Absolute path to repository root.
            repo_map: Scanned repository structure.
            import_graph: Import dependencies.
            descriptors: File summaries.
        """
        # Collect file mtimes for validation
        file_mtimes = {}
        for rel_path, node in repo_map.files.items():
            if not node.is_dir:
                file_mtimes[rel_path] = node.modified_time
        
        # Create cache entry
        entry = CacheEntry(
            repo_path=repo_path,
            repo_map=repo_map,
            import_graph=import_graph,
            descriptors=descriptors,
            cache_time=time.time(),
            file_mtimes=file_mtimes,
        )
        
        # Store in cache
        self.cache_manager.put(entry)
        logger.info("Stored cache for %s", repo_path)
    
    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------
    
    def invalidate_cache(self, repo_path: Path) -> None:
        """Manually invalidate cache for a repository.
        
        Useful for forcing re-analysis after known changes.
        
        Args:
            repo_path: Absolute path to repository root.
        """
        repo_path = repo_path.resolve()
        self.cache_manager.invalidate(repo_path)
        logger.info("Manually invalidated cache for %s", repo_path)
    
    def clear_all_caches(self) -> None:
        """Clear all cached repository analyses.
        
        Removes all cache files from the cache directory.
        """
        if not self.config.enable_cache:
            logger.warning("Cache is disabled, nothing to clear")
            return
        
        cache_dir = self.cache_manager.cache_dir
        if not cache_dir.exists():
            logger.info("Cache directory does not exist, nothing to clear")
            return
        
        count = 0
        for cache_file in cache_dir.glob("repo_*.json"):
            try:
                cache_file.unlink()
                count += 1
            except Exception as e:
                logger.warning("Failed to delete %s: %s", cache_file, e)
        
        logger.info("Cleared %d cache files from %s", count, cache_dir)
