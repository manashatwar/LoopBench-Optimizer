"""
Repository-to-Context Mapper

This package provides intelligent repository context for LLM-based code optimization.
It analyzes repository structure, identifies relevant files through import analysis,
and generates context-aware prompts.
"""

from openevolve.repo_mapper.models import (
    FileNode,
    RepositoryMap,
    RepoMapperConfig,
    ImportRelation,
    ImportGraph,
    FileDescriptor,
    RelevanceScore,
    ContextMap,
)
from openevolve.repo_mapper.scanner import RepositoryScanner, DEFAULT_IGNORE_PATTERNS
from openevolve.repo_mapper.parser_interface import (
    ImportInfo,
    ClassInfo,
    FunctionInfo,
    FileStructure,
    extract_imports,
    extract_structure,
)
from openevolve.repo_mapper.import_analyzer import ImportAnalyzer
from openevolve.repo_mapper.file_analyzer import FileAnalyzer
from openevolve.repo_mapper.relevance_scorer import RelevanceScorer
from openevolve.repo_mapper.context_builder import ContextBuilder
from openevolve.repo_mapper.cache_manager import CacheManager, CacheEntry
from openevolve.repo_mapper.mapper import (
    RepoContextMapper,
    RepoMapperError,
    RepositoryScanError,
    ImportAnalysisError,
    CacheError,
    ContextBuildError,
)

__all__ = [
    # Models
    "FileNode",
    "RepositoryMap",
    "RepoMapperConfig",
    "ImportRelation",
    "ImportGraph",
    "FileDescriptor",
    "RelevanceScore",
    "ContextMap",
    # Scanner
    "RepositoryScanner",
    "DEFAULT_IGNORE_PATTERNS",
    # Parser interface
    "ImportInfo",
    "ClassInfo",
    "FunctionInfo",
    "FileStructure",
    "extract_imports",
    "extract_structure",
    # Import analyzer
    "ImportAnalyzer",
    # File analyzer
    "FileAnalyzer",
    # Relevance scorer
    "RelevanceScorer",
    # Context builder
    "ContextBuilder",
    # Cache manager
    "CacheManager",
    "CacheEntry",
    # Main orchestrator
    "RepoContextMapper",
    # Errors
    "RepoMapperError",
    "RepositoryScanError",
    "ImportAnalysisError",
    "CacheError",
    "ContextBuildError",
]
