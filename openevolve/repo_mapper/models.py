"""
Data models for the Repository-to-Context Mapper.

This module defines the core data structures used throughout the repo mapper system:
- RepoMapperConfig: Configuration for scanning, analysis, and performance
- FileNode: Representation of a file or directory in the repository tree
- RepositoryMap: Complete repository structure with metadata
- ImportRelation: Representation of an import dependency between files
- ImportGraph: Directed graph of import relationships in the repository
- FileDescriptor: Summary of a file's purpose and structure (Task 3.1)
- RelevanceScore: Per-file relevance score breakdown (Task 3.3)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Tuple


@dataclass
class RepoMapperConfig:
    """Configuration for Repository Mapper behavior.
    
    This dataclass contains all configuration options for scanning, analysis,
    token budget management, caching, and performance tuning.
    """
    
    # Scanning Configuration
    ignore_patterns: List[str] = field(default_factory=lambda: [
        # Version control
        ".git", ".svn", ".hg",
        # Python artifacts
        "__pycache__", "*.pyc", "*.pyo", "*.pyd",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "*.egg-info", "dist", "build", ".eggs",
        # Virtual environments
        ".venv", "venv", "env", "ENV",
        # IDE and editor files
        ".vscode", ".idea", "*.swp", "*.swo", "*~",
        # Dependencies
        "node_modules", "vendor",
        # Build artifacts
        "*.o", "*.so", "*.dylib", "*.dll",
        # Logs and temporary files
        "*.log", "*.tmp", ".DS_Store",
    ])
    max_traversal_depth: int = 10
    max_file_size_bytes: int = 10_000_000  # 10MB limit
    
    # Analysis Configuration
    max_relevant_files: int = 10
    max_file_descriptor_length: int = 200
    parse_timeout_seconds: float = 5.0
    
    # Token Budget Configuration
    token_budget: int = 2000
    estimate_tokens_per_char: float = 0.25  # Rough approximation
    
    # Caching Configuration
    enable_cache: bool = True
    cache_dir: Optional[Path] = None  # If None, uses temp directory
    cache_ttl_seconds: float = 3600.0  # 1 hour
    
    # Performance Configuration
    parallel_analysis: bool = True
    max_workers: int = 4


@dataclass
class FileNode:
    """Represents a file or directory in the repository tree.
    
    Contains metadata about a single file or directory, including its location,
    size, modification time, and depth in the tree structure.
    """
    
    path: Path  # Relative to repository root
    absolute_path: Path  # Full filesystem path
    is_dir: bool
    size_bytes: int
    modified_time: float  # Unix timestamp
    depth: int  # Distance from repository root (root = 0)
    
    def __str__(self) -> str:
        """String representation showing path and type."""
        type_str = "dir" if self.is_dir else "file"
        return f"{type_str}: {self.path}"
    
    def __repr__(self) -> str:
        """Detailed representation with all fields."""
        return (
            f"FileNode(path={self.path}, is_dir={self.is_dir}, "
            f"size={self.size_bytes}, depth={self.depth})"
        )


@dataclass
class RepositoryMap:
    """Complete repository structure with metadata.
    
    Represents the entire scanned repository as a tree of FileNodes,
    with utilities for serialization and navigation.
    """
    
    repo_path: Path  # Absolute path to repository root
    root_node: FileNode  # Root directory node
    files: Dict[Path, FileNode]  # Map of relative_path -> FileNode
    scan_timestamp: float  # Unix timestamp when scan was performed
    
    def to_tree_string(self, max_depth: Optional[int] = None) -> str:
        """Serialize repository tree to indented text format for LLM consumption.
        
        Args:
            max_depth: Maximum depth to display. If None, shows all levels.
            
        Returns:
            Formatted tree string with indentation showing hierarchy.
            
        Example:
            repo_root/
              src/
                main.py
                utils.py
              tests/
                test_main.py
        """
        lines: List[str] = []
        
        # Start with root
        lines.append(f"{self.root_node.path.name or 'root'}/")
        
        # Sort files by path for consistent output
        sorted_files = sorted(self.files.values(), key=lambda f: f.path)
        
        for file_node in sorted_files:
            # Skip if exceeds max depth
            if max_depth is not None and file_node.depth > max_depth:
                continue
            
            # Create indentation based on depth
            indent = "  " * file_node.depth
            
            # Format name with trailing slash for directories
            name = file_node.path.name
            if file_node.is_dir:
                name += "/"
            
            lines.append(f"{indent}{name}")
        
        return "\n".join(lines)
    
    def get_file(self, relative_path: Path) -> Optional[FileNode]:
        """Get a FileNode by its relative path.
        
        Args:
            relative_path: Path relative to repository root
            
        Returns:
            FileNode if found, None otherwise
        """
        return self.files.get(relative_path)
    
    def get_files_in_directory(self, dir_path: Path) -> List[FileNode]:
        """Get all direct children of a directory.
        
        Args:
            dir_path: Directory path relative to repository root
            
        Returns:
            List of FileNodes that are direct children of the directory
        """
        result = []
        for file_node in self.files.values():
            if file_node.path.parent == dir_path:
                result.append(file_node)
        return result
    
    def __repr__(self) -> str:
        """Detailed representation showing repository info."""
        return (
            f"RepositoryMap(repo_path={self.repo_path}, "
            f"files_count={len(self.files)}, "
            f"scan_timestamp={self.scan_timestamp})"
        )


@dataclass
class ImportRelation:
    """Represents an import dependency between files.
    
    Records a single import statement, including the source file, target module,
    resolved target file (if resolvable), import type, and line number.
    """
    
    source_file: Path  # File containing the import statement (relative to repo root)
    target_module: str  # Imported module name (e.g., "openevolve.config", "numpy")
    target_file: Optional[Path]  # Resolved file path (relative to repo root, None if external/unresolvable)
    import_type: str  # "absolute", "relative", "stdlib", "third_party"
    line_number: int  # Line number where import appears in source file
    
    def __str__(self) -> str:
        """String representation showing import relationship."""
        target = self.target_file if self.target_file else self.target_module
        return f"{self.source_file} -> {target} (line {self.line_number})"
    
    def __repr__(self) -> str:
        """Detailed representation with all fields."""
        return (
            f"ImportRelation(source={self.source_file}, "
            f"target_module={self.target_module}, "
            f"target_file={self.target_file}, "
            f"type={self.import_type}, "
            f"line={self.line_number})"
        )


@dataclass
class ImportGraph:
    """Directed graph of import relationships in the repository.
    
    Maintains a list of all import relations and adjacency dictionaries for
    efficient lookup of direct and reverse dependencies.
    """
    
    relations: List[ImportRelation] = field(default_factory=list)  # All import relations
    adjacency: Dict[Path, Set[Path]] = field(default_factory=dict)  # source -> {targets}
    reverse_adjacency: Dict[Path, Set[Path]] = field(default_factory=dict)  # target -> {sources}
    
    def get_direct_imports(self, file: Path) -> Set[Path]:
        """Get files directly imported by the specified file.
        
        Args:
            file: Path to source file (relative to repo root)
            
        Returns:
            Set of file paths that are imported by the source file.
            Empty set if file has no imports or is not in graph.
        """
        return self.adjacency.get(file, set())
    
    def get_reverse_imports(self, file: Path) -> Set[Path]:
        """Get files that import the specified file.
        
        Args:
            file: Path to target file (relative to repo root)
            
        Returns:
            Set of file paths that import the target file.
            Empty set if file is not imported by any other file.
        """
        return self.reverse_adjacency.get(file, set())
    
    def add_relation(self, relation: ImportRelation) -> None:
        """Add an import relation to the graph and update adjacency dictionaries.
        
        Args:
            relation: ImportRelation to add
        """
        # Add to relations list
        self.relations.append(relation)
        
        # Update adjacency (source -> target) if target is resolvable
        if relation.target_file is not None:
            if relation.source_file not in self.adjacency:
                self.adjacency[relation.source_file] = set()
            self.adjacency[relation.source_file].add(relation.target_file)
            
            # Update reverse adjacency (target -> sources)
            if relation.target_file not in self.reverse_adjacency:
                self.reverse_adjacency[relation.target_file] = set()
            self.reverse_adjacency[relation.target_file].add(relation.source_file)
    
    def get_all_files(self) -> Set[Path]:
        """Get all files that appear in the import graph (either as source or target).
        
        Returns:
            Set of all file paths in the graph
        """
        files = set()
        files.update(self.adjacency.keys())
        files.update(self.reverse_adjacency.keys())
        return files
    
    def has_file(self, file: Path) -> bool:
        """Check if a file appears in the import graph.
        
        Args:
            file: Path to check (relative to repo root)
            
        Returns:
            True if file is in the graph, False otherwise
        """
        return file in self.adjacency or file in self.reverse_adjacency
    
    def __len__(self) -> int:
        """Return the number of import relations in the graph."""
        return len(self.relations)
    
    def __repr__(self) -> str:
        """Detailed representation showing graph statistics."""
        num_files = len(self.get_all_files())
        return (
            f"ImportGraph(relations={len(self.relations)}, "
            f"files={num_files})"
        )


# ---------------------------------------------------------------------------
# Task 3.1 — FileDescriptor
# ---------------------------------------------------------------------------

@dataclass
class FileDescriptor:
    """Summary of a file's purpose, structure, and role.

    Used by :class:`ContextBuilder` to format concise per-file descriptions
    for LLM context maps.

    Attributes:
        file_path: Relative path from repository root.
        role: Inferred role string (e.g. ``"test"``, ``"utility"``, ``"config"``)
        summary: Concise human-readable description (max ``max_file_descriptor_length`` chars).
        classes: List of top-level class names defined in the file.
        functions: List of top-level function names defined in the file.
        has_main: True if the file has an ``if __name__ == '__main__'`` block.
        loc: Approximate lines-of-code count.
    """

    file_path: Path
    role: str
    summary: str
    classes: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    has_main: bool = False
    loc: int = 0

    def to_string(self, include_score: Optional[float] = None) -> str:
        """Format descriptor as a structured text block for LLM context maps.

        Args:
            include_score: Optional relevance score to display next to file name.

        Returns:
            Multi-line text block suitable for embedding in a prompt.

        Example output::

            **evaluator.py** (score: 0.84, role: utility)
            Contains performance evaluation logic.
            Classes: TaskEvaluator
            Functions: evaluate_performance, load_config
        """
        score_str = f" (score: {include_score:.2f})" if include_score is not None else ""
        lines = [f"**{self.file_path.name}**{score_str} [role: {self.role}, loc: {self.loc}]"]
        lines.append(self.summary)
        if self.classes:
            lines.append(f"Classes: {', '.join(self.classes)}")
        if self.functions:
            lines.append(f"Functions: {', '.join(self.functions)}")
        if self.has_main:
            lines.append("Entry point: yes")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"FileDescriptor(path={self.file_path}, role={self.role!r}, "
            f"classes={len(self.classes)}, functions={len(self.functions)})"
        )


# ---------------------------------------------------------------------------
# Task 3.3 — RelevanceScore
# ---------------------------------------------------------------------------

@dataclass
class RelevanceScore:
    """Relevance score for a single file relative to an optimization target.

    Stores both the combined ``total_score`` and the four component scores that
    feed into it, making it possible to explain *why* a file was ranked highly.

    Component scores are all in ``[0.0, 1.0]``.

    Attributes:
        file_path: Relative path of the scored file.
        total_score: Weighted combination of all component scores (``[0.0, 1.0]``).
        direct_import_score: How directly the target imports this file (hop distance).
        reverse_import_score: Whether this file imports the target (reverse dep).
        directory_proximity_score: How close this file is in directory tree.
        name_similarity_score: Filename similarity to the target.
    """

    file_path: Path
    total_score: float
    direct_import_score: float = 0.0
    reverse_import_score: float = 0.0
    directory_proximity_score: float = 0.0
    name_similarity_score: float = 0.0

    def __repr__(self) -> str:
        return (
            f"RelevanceScore(path={self.file_path}, total={self.total_score:.3f}, "
            f"direct={self.direct_import_score:.2f}, "
            f"prox={self.directory_proximity_score:.2f}, "
            f"rev={self.reverse_import_score:.2f}, "
            f"name={self.name_similarity_score:.2f})"
        )


# ---------------------------------------------------------------------------
# Task 5.1 — ContextMap
# ---------------------------------------------------------------------------

@dataclass
class ContextMap:
    """Structured context for LLM prompts, respecting token budgets.

    Contains the target file being optimised, relevant files sorted by
    relevance score, a filtered repository tree, and token accounting.

    Generated by :class:`ContextBuilder` in Task 5.2.

    Attributes:
        target_file: Relative path of the file being optimised.
        target_descriptor: :class:`FileDescriptor` for the target.
        relevant_files: List of (path, descriptor, score) tuples for relevant
            files, sorted by descending relevance score.
        repository_tree: Filtered tree showing only relevant files and ancestors.
        token_count: Estimated total tokens in the context map.
    """

    target_file: Path
    target_descriptor: FileDescriptor
    relevant_files: List[Tuple[Path, FileDescriptor, float]] = field(default_factory=list)
    repository_tree: str = ""
    token_count: int = 0

    def to_prompt_section(self) -> str:
        """Format context map as a structured prompt section for LLMs.

        Returns:
            Multi-line text block suitable for insertion into a prompt.

        Example output::

            ## Repository Context

            Repository: OpenEvolve
            Target File: examples/algotune/affine_transform_2d/initial_program.py

            ### File Structure
            ```
            examples/
              algotune/
                affine_transform_2d/
                  initial_program.py  <- Relevant
                  evaluator.py  <- Relevant
                  config.yaml  <- Relevant
            ```

            ### Target File
            **initial_program.py** [role: main, loc: 120]
            Main optimization target implementing affine transformation algorithm.
            Functions: affine_transform, benchmark_performance
            Entry point: yes

            ### Relevant Files

            **evaluator.py** (score: 0.84) [role: utility, loc: 95]
            Contains performance evaluation logic for the optimization task.
            Classes: TaskEvaluator
            Functions: evaluate_performance, load_config

            **config.yaml** (score: 0.32) [role: config, loc: 20]
            Configuration file specifying optimization parameters.
        """
        lines = []
        lines.append("## Repository Context")
        lines.append("")
        lines.append(f"Target File: {self.target_file}")
        lines.append("")

        # File structure tree
        lines.append("### File Structure")
        lines.append("```")
        lines.append(self.repository_tree)
        lines.append("```")
        lines.append("")

        # Target file descriptor
        lines.append("### Target File")
        lines.append(self.target_descriptor.to_string())
        lines.append("")

        # Relevant files (if any)
        if self.relevant_files:
            lines.append("### Relevant Files")
            lines.append("")
            for path, descriptor, score in self.relevant_files:
                lines.append(descriptor.to_string(include_score=score))
                lines.append("")  # Blank line between files

        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"ContextMap(target={self.target_file}, "
            f"relevant_files={len(self.relevant_files)}, "
            f"tokens={self.token_count})"
        )
