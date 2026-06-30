# Repository Context Mapper - API Reference

## Table of Contents

1. [RepoContextMapper](#repocontextmapper)
2. [Configuration Models](#configuration-models)
3. [Data Models](#data-models)
4. [Component APIs](#component-apis)
5. [Error Types](#error-types)
6. [Usage Examples](#usage-examples)

---

## RepoContextMapper

Main orchestrator class for repository analysis.

### Class: `RepoContextMapper`

**Location**: `openevolve/repo_mapper/__init__.py`

#### Constructor

```python
def __init__(self, config: RepoMapperConfig)
```

**Parameters**:
- `config` (`RepoMapperConfig`): Configuration object

**Example**:
```python
from openevolve.repo_mapper import RepoContextMapper
from openevolve.repo_mapper.models import RepoMapperConfig

config = RepoMapperConfig(
    enable_cache=True,
    token_budget=1500,
)
mapper = RepoContextMapper(config)
```

---

#### Method: `get_context_map`

Generate context map for a target file.

```python
def get_context_map(
    self,
    repo_path: Path,
    target_file: Path,
) -> ContextMap
```

**Parameters**:
- `repo_path` (`Path`): Absolute path to repository root
- `target_file` (`Path`): Absolute path to target file within repository

**Returns**:
- `ContextMap`: Context map containing target file info, relevant files, and file structure

**Raises**:
- `RepositoryScanError`: If repository scan fails
- `ImportAnalysisError`: If import analysis fails  
- `ContextBuildError`: If context building fails

**Example**:
```python
from pathlib import Path

context_map = mapper.get_context_map(
    repo_path=Path("/path/to/repo"),
    target_file=Path("/path/to/repo/main.py"),
)

# Use context in LLM prompt
prompt_context = context_map.to_prompt_section()
```

---

#### Method: `get_repository_map`

Get repository file tree (with optional caching).

```python
def get_repository_map(
    self,
    repo_path: Path,
) -> RepositoryMap
```

**Parameters**:
- `repo_path` (`Path`): Absolute path to repository root

**Returns**:
- `RepositoryMap`: File tree of repository

**Raises**:
- `RepositoryScanError`: If scan fails

**Example**:
```python
repo_map = mapper.get_repository_map(Path("/path/to/repo"))
print(f"Found {len(repo_map.files)} files")
print(repo_map.to_tree_string())
```

---

## Configuration Models

### Class: `RepoMapperConfig`

**Location**: `openevolve/repo_mapper/models.py`

Configuration for repository analysis.

```python
@dataclass
class RepoMapperConfig:
    # Scanning configuration
    ignore_patterns: List[str] = field(default_factory=lambda: [
        '.git/', 'node_modules/', '__pycache__/', '.venv/',
        'dist/', 'build/', '.mypy_cache/', '.pytest_cache/'
    ])
    max_depth: int = 10
    follow_symlinks: bool = False
    
    # Analysis configuration
    max_file_size_bytes: int = 1024 * 1024  # 1MB
    max_file_descriptor_length: int = 200
    
    # Token budget
    token_budget: int = 1500
    max_relevant_files: int = 10
    
    # Caching configuration
    enable_cache: bool = True
    cache_dir: Optional[Path] = None  # Defaults to ~/.cache/openevolve/repo_mapper
    
    # Performance configuration
    max_workers: int = 4  # For parallel file analysis
```

**Field Descriptions**:

**Scanning**:
- `ignore_patterns`: Glob patterns to exclude from scan
- `max_depth`: Maximum directory depth to scan
- `follow_symlinks`: Whether to follow symbolic links

**Analysis**:
- `max_file_size_bytes`: Skip files larger than this
- `max_file_descriptor_length`: Truncate descriptors to this length

**Token Budget**:
- `token_budget`: Maximum tokens for context map
- `max_relevant_files`: Maximum files to include

**Caching**:
- `enable_cache`: Whether to cache analysis results
- `cache_dir`: Directory for cache files (None = default)

**Performance**:
- `max_workers`: Parallel workers for file analysis

**Example**:
```python
# Minimal configuration
config = RepoMapperConfig()

# Custom configuration
config = RepoMapperConfig(
    token_budget=2000,
    max_relevant_files=5,
    enable_cache=False,
    ignore_patterns=['.git/', '__pycache__/', 'node_modules/'],
)
```

---

## Data Models

### Class: `ContextMap`

**Location**: `openevolve/repo_mapper/models.py`

Final context map for LLM prompts.

```python
@dataclass
class ContextMap:
    target_file: Path
    target_descriptor: FileDescriptor
    relevant_files: List[Tuple[FileDescriptor, RelevanceScore]]
    repository_tree: str
    token_count: int
```

**Fields**:
- `target_file`: Path to target file
- `target_descriptor`: Descriptor of target file
- `relevant_files`: List of (descriptor, score) tuples, sorted by relevance
- `repository_tree`: Filtered file tree showing only relevant files
- `token_count`: Estimated total tokens in context

#### Method: `to_prompt_section`

Format context for insertion into LLM prompt.

```python
def to_prompt_section(self) -> str
```

**Returns**:
- `str`: Markdown-formatted context section

**Example**:
```python
context_text = context_map.to_prompt_section()
# Output:
# ## Repository Context
# 
# Target File: main.py
# 
# ### File Structure
# ```
# repo/
#   main.py  <- Target
#   utils.py  <- Relevant
# ```
# ...
```

---

### Class: `FileDescriptor`

**Location**: `openevolve/repo_mapper/models.py`

Description of a single file.

```python
@dataclass
class FileDescriptor:
    file_path: Path
    role: str  # 'main', 'test', 'config', 'utility', 'model', 'interface'
    summary: str
    classes: List[str]
    functions: List[str]
    has_main: bool  # Has if __name__ == '__main__'
    loc: int  # Lines of code
```

**Fields**:
- `file_path`: Relative path from repository root
- `role`: Inferred role (main, test, config, utility, model, interface)
- `summary`: Brief description (from docstring or generated)
- `classes`: List of class names defined in file
- `functions`: List of function names defined in file
- `has_main`: Whether file has entry point
- `loc`: Lines of code (non-empty, non-comment)

#### Method: `to_string`

Format descriptor for display.

```python
def to_string(self) -> str
```

**Returns**:
- `str`: Formatted descriptor

**Example**:
```python
print(descriptor.to_string())
# Output:
# **main.py** [role: main, loc: 50]
# Main entry point. Processes data using utils.
# Functions: main, process_data
# Entry point: yes
```

---

### Class: `RelevanceScore`

**Location**: `openevolve/repo_mapper/models.py`

Relevance score for a file relative to target.

```python
@dataclass
class RelevanceScore:
    file_path: Path
    total_score: float  # 0.0 - 1.0
    
    # Component scores
    direct_import_score: float  # 0.0 - 1.0
    reverse_import_score: float  # 0.0 - 1.0
    directory_proximity_score: float  # 0.0 - 1.0
    name_similarity_score: float  # 0.0 - 1.0
```

**Fields**:
- `file_path`: Path to scored file
- `total_score`: Weighted total (0.0 - 1.0)
- `direct_import_score`: Score based on import dependencies
- `reverse_import_score`: Score based on reverse dependencies
- `directory_proximity_score`: Score based on directory distance
- `name_similarity_score`: Score based on name similarity

**Scoring Formula**:
```python
total_score = (
    0.50 * direct_import_score +
    0.30 * directory_proximity_score +
    0.15 * reverse_import_score +
    0.05 * name_similarity_score
)
```

---

### Class: `RepositoryMap`

**Location**: `openevolve/repo_mapper/models.py`

File tree of repository.

```python
@dataclass
class RepositoryMap:
    repo_path: Path
    root_node: FileNode
    files: Dict[Path, FileNode]  # All files (flat lookup)
    scan_timestamp: float
```

**Fields**:
- `repo_path`: Absolute path to repository root
- `root_node`: Root of file tree
- `files`: Flat dictionary of all files for fast lookup
- `scan_timestamp`: When scan was performed

#### Method: `to_tree_string`

Generate tree view of repository.

```python
def to_tree_string(
    self,
    max_depth: Optional[int] = None,
    highlight_files: Optional[Set[Path]] = None,
) -> str
```

**Parameters**:
- `max_depth`: Maximum depth to show (None = unlimited)
- `highlight_files`: Files to mark with arrows

**Returns**:
- `str`: Tree representation

**Example**:
```python
tree = repo_map.to_tree_string(
    max_depth=3,
    highlight_files={Path("main.py"), Path("utils.py")}
)
print(tree)
# Output:
# repo/
#   src/
#     main.py  <- Target
#     utils.py  <- Relevant
#   tests/
#     test_main.py
```

---

### Class: `ImportGraph`

**Location**: `openevolve/repo_mapper/models.py`

Import dependency graph.

```python
@dataclass
class ImportGraph:
    relations: List[ImportRelation]
    direct_imports: Dict[Path, Set[Path]]  # file -> files it imports
    reverse_imports: Dict[Path, Set[Path]]  # file -> files that import it
```

**Fields**:
- `relations`: List of all import relations
- `direct_imports`: Adjacency dict (file -> imported files)
- `reverse_imports`: Reverse adjacency dict (file -> importers)

#### Method: `get_direct_imports`

Get files directly imported by a file.

```python
def get_direct_imports(self, file: Path) -> Set[Path]
```

#### Method: `get_reverse_imports`

Get files that import a file.

```python
def get_reverse_imports(self, file: Path) -> Set[Path]
```

**Example**:
```python
# Get what main.py imports
imports = import_graph.get_direct_imports(Path("main.py"))
# {Path("utils.py"), Path("config.py")}

# Get what imports main.py
importers = import_graph.get_reverse_imports(Path("main.py"))
# {Path("tests/test_main.py")}
```

---

### Class: `ImportRelation`

**Location**: `openevolve/repo_mapper/models.py`

Single import relationship.

```python
@dataclass
class ImportRelation:
    source_file: Path  # File that contains the import
    target_module: str  # Module name (e.g., "utils" or "os.path")
    target_file: Optional[Path]  # Resolved file path (None if external)
    import_type: str  # 'import' or 'from'
    line_number: int
```

---

### Class: `FileNode`

**Location**: `openevolve/repo_mapper/models.py`

Node in file tree.

```python
@dataclass
class FileNode:
    path: Path  # Relative path from repo root
    absolute_path: Path
    is_dir: bool
    size_bytes: int
    modified_time: float
    depth: int
    children: Dict[str, FileNode] = field(default_factory=dict)
```

---

## Component APIs

### RepositoryScanner

**Location**: `openevolve/repo_mapper/scanner.py`

Scans repository filesystem.

```python
class RepositoryScanner:
    def __init__(self, config: RepoMapperConfig):
        ...
    
    def scan(self, repo_path: Path) -> RepositoryMap:
        """Scan repository and build file tree"""
        ...
```

---

### ImportAnalyzer

**Location**: `openevolve/repo_mapper/import_analyzer.py`

Analyzes import dependencies.

```python
class ImportAnalyzer:
    def __init__(self, config: RepoMapperConfig):
        ...
    
    def analyze(
        self,
        repo_path: Path,
        repo_map: RepositoryMap,
    ) -> ImportGraph:
        """Build import dependency graph"""
        ...
```

---

### FileAnalyzer

**Location**: `openevolve/repo_mapper/file_analyzer.py`

Analyzes file structure and infers roles.

```python
class FileAnalyzer:
    def __init__(self, config: RepoMapperConfig):
        ...
    
    def analyze_file(self, file_path: Path) -> FileDescriptor:
        """Generate descriptor for a file"""
        ...
```

---

### RelevanceScorer

**Location**: `openevolve/repo_mapper/relevance_scorer.py`

Scores files by relevance to target.

```python
class RelevanceScorer:
    def __init__(
        self,
        config: RepoMapperConfig,
        weights: Optional[Dict[str, float]] = None,
    ):
        ...
    
    def score_files(
        self,
        target_file: Path,
        all_files: List[FileNode],
        import_graph: ImportGraph,
    ) -> List[Tuple[Path, RelevanceScore]]:
        """Score all files by relevance to target"""
        ...
```

**Custom Weights Example**:
```python
# Default weights
weights = {
    'direct_import': 0.50,
    'directory_proximity': 0.30,
    'reverse_import': 0.15,
    'name_similarity': 0.05,
}

# Custom weights (emphasize imports)
custom_weights = {
    'direct_import': 0.70,
    'directory_proximity': 0.20,
    'reverse_import': 0.10,
    'name_similarity': 0.00,
}

scorer = RelevanceScorer(config, weights=custom_weights)
```

---

### ContextBuilder

**Location**: `openevolve/repo_mapper/context_builder.py`

Builds context maps within token budget.

```python
class ContextBuilder:
    def __init__(self, config: RepoMapperConfig):
        ...
    
    def build(
        self,
        target_file: Path,
        target_descriptor: FileDescriptor,
        relevance_scores: List[Tuple[Path, RelevanceScore]],
        descriptors: Dict[Path, FileDescriptor],
        repo_map: RepositoryMap,
    ) -> ContextMap:
        """Build context map within token budget"""
        ...
```

---

### CacheManager

**Location**: `openevolve/repo_mapper/cache_manager.py`

Manages analysis result caching.

```python
class CacheManager:
    def __init__(self, config: RepoMapperConfig):
        ...
    
    def get(self, repo_path: Path) -> Optional[CacheEntry]:
        """Get cached entry if valid"""
        ...
    
    def put(self, repo_path: Path, entry: CacheEntry) -> None:
        """Store entry in cache"""
        ...
    
    def is_valid(self, entry: CacheEntry, repo_path: Path) -> bool:
        """Check if cache entry is still valid"""
        ...
    
    def invalidate(self, repo_path: Path) -> None:
        """Remove cache entry"""
        ...
```

**Example**:
```python
# Manual cache management
cache = CacheManager(config)

# Check cache
entry = cache.get(repo_path)
if entry and cache.is_valid(entry, repo_path):
    print("Cache hit!")
else:
    print("Cache miss, regenerating...")

# Clear cache manually
cache.invalidate(repo_path)
```

---

## Error Types

### Exception: `RepoMapperError`

Base exception for all repo mapper errors.

```python
class RepoMapperError(Exception):
    """Base exception for repo mapper"""
    def __init__(self, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.error_code = error_code
```

---

### Exception: `RepositoryScanError`

Error during repository scanning.

```python
class RepositoryScanError(RepoMapperError):
    """Error scanning repository"""
```

**Common Causes**:
- Permission denied
- Invalid repository path
- Filesystem errors

---

### Exception: `ImportAnalysisError`

Error during import analysis.

```python
class ImportAnalysisError(RepoMapperError):
    """Error analyzing imports"""
```

**Common Causes**:
- Parse errors in Python files
- Tool (grep-ast) failures

---

### Exception: `CacheError`

Error with cache operations.

```python
class CacheError(RepoMapperError):
    """Error with cache operations"""
```

**Common Causes**:
- Cache corruption (malformed JSON)
- Permission errors on cache directory
- Disk full

---

### Exception: `ContextBuildError`

Error building context map.

```python
class ContextBuildError(RepoMapperError):
    """Error building context"""
```

**Common Causes**:
- Token budget too small
- No relevant files found
- Missing descriptors

---

## Usage Examples

### Example 1: Basic Usage

```python
from openevolve.repo_mapper import RepoContextMapper
from openevolve.repo_mapper.models import RepoMapperConfig
from pathlib import Path

# Configure
config = RepoMapperConfig(
    enable_cache=True,
    token_budget=1500,
)

# Create mapper
mapper = RepoContextMapper(config)

# Analyze
context_map = mapper.get_context_map(
    repo_path=Path("/path/to/repo"),
    target_file=Path("/path/to/repo/main.py"),
)

# Use in prompt
print(context_map.to_prompt_section())
```

---

### Example 2: Custom Configuration

```python
# Custom configuration
config = RepoMapperConfig(
    # Scanning
    ignore_patterns=['.git/', '__pycache__/', 'venv/', 'build/'],
    max_depth=5,
    follow_symlinks=False,
    
    # Analysis
    max_file_size_bytes=2 * 1024 * 1024,  # 2MB
    max_file_descriptor_length=150,
    
    # Token budget
    token_budget=2000,
    max_relevant_files=8,
    
    # Caching
    enable_cache=True,
    cache_dir=Path.home() / '.my_cache',
    
    # Performance
    max_workers=8,
)

mapper = RepoContextMapper(config)
```

---

### Example 3: Error Handling

```python
from openevolve.repo_mapper import RepoContextMapper, RepoMapperError
from openevolve.repo_mapper.models import RepoMapperConfig

try:
    mapper = RepoContextMapper(config)
    context_map = mapper.get_context_map(repo_path, target_file)
    
except RepositoryScanError as e:
    print(f"Scan failed: {e}")
    print(f"Error code: {e.error_code}")
    
except ImportAnalysisError as e:
    print(f"Import analysis failed: {e}")
    # Continue without import analysis
    
except ContextBuildError as e:
    print(f"Context build failed: {e}")
    # Use fallback context
    
except RepoMapperError as e:
    print(f"General error: {e}")
```

---

### Example 4: Integration with PromptSampler

```python
from openevolve.config import PromptConfig
from openevolve.prompt.sampler import PromptSampler
from openevolve.repo_mapper.models import RepoMapperConfig

# Configure prompt with repo mapper
config = PromptConfig(
    repo_mapper=RepoMapperConfig(
        enable_cache=True,
        token_budget=1500,
    )
)

# Create sampler (automatically initializes repo mapper)
sampler = PromptSampler(config)

# Generate context-aware prompt
prompt = sampler.build_prompt(
    current_program=code,
    program_metrics={"performance": 0.5},
    language="python",
    repo_path="/path/to/repo",
    target_file="/path/to/repo/main.py",
)

# Prompt includes repository context!
print(prompt["user"])
```

---

### Example 5: Manual Component Usage

```python
# Use components individually for debugging
from openevolve.repo_mapper.scanner import RepositoryScanner
from openevolve.repo_mapper.import_analyzer import ImportAnalyzer
from openevolve.repo_mapper.relevance_scorer import RelevanceScorer

# Scan
scanner = RepositoryScanner(config)
repo_map = scanner.scan(repo_path)
print(f"Found {len(repo_map.files)} files")

# Analyze imports
analyzer = ImportAnalyzer(config)
import_graph = analyzer.analyze(repo_path, repo_map)
print(f"Found {len(import_graph.relations)} import relations")

# Score relevance
scorer = RelevanceScorer(config)
scores = scorer.score_files(target_file, list(repo_map.files.values()), import_graph)

# Print top 10
for file_path, score in sorted(scores, key=lambda x: x[1].total_score, reverse=True)[:10]:
    print(f"{file_path}: {score.total_score:.2f}")
```

---

### Example 6: Cache Management

```python
from openevolve.repo_mapper.cache_manager import CacheManager

# Create cache manager
cache = CacheManager(config)

# Check if cached
entry = cache.get(repo_path)
if entry and cache.is_valid(entry, repo_path):
    print("Using cached analysis")
    repo_map = entry.repo_map
    import_graph = entry.import_graph
else:
    print("Cache miss, analyzing...")
    # Perform analysis
    repo_map = scanner.scan(repo_path)
    import_graph = analyzer.analyze(repo_path, repo_map)
    
    # Cache results
    cache.put(repo_path, CacheEntry(
        repo_path=repo_path,
        repo_map=repo_map,
        import_graph=import_graph,
        ...
    ))

# Manually invalidate
cache.invalidate(repo_path)
```

---

## Configuration via YAML

### PromptConfig with RepoMapper

```yaml
# config.yaml
prompt:
  repo_mapper:
    # Scanning
    ignore_patterns:
      - '.git/'
      - '__pycache__/'
      - 'node_modules/'
    max_depth: 10
    follow_symlinks: false
    
    # Analysis
    max_file_size_bytes: 1048576  # 1MB
    max_file_descriptor_length: 200
    
    # Token budget
    token_budget: 1500
    max_relevant_files: 10
    
    # Caching
    enable_cache: true
    # cache_dir: null  # Uses default
    
    # Performance
    max_workers: 4
```

### Loading Configuration

```python
from openevolve.config import Config

# Load from YAML
config = Config.from_yaml("config.yaml")

# Repo mapper automatically configured
sampler = PromptSampler(config.prompt)
```

---

## Best Practices

### 1. Enable Caching in Development
```python
config = RepoMapperConfig(enable_cache=True)
```
**Reason**: 10-100x speedup on repeated analyses

### 2. Adjust Token Budget Based on LLM
```python
# For GPT-4 (8k context)
config = RepoMapperConfig(token_budget=1500)

# For GPT-4-32k
config = RepoMapperConfig(token_budget=5000)

# For Claude (100k context)
config = RepoMapperConfig(token_budget=10000)
```

### 3. Use Custom Ignore Patterns for Large Repos
```python
config = RepoMapperConfig(
    ignore_patterns=[
        '.git/', '__pycache__/', 'node_modules/',
        'vendor/', 'third_party/', 'external/',
    ]
)
```

### 4. Handle Errors Gracefully
```python
try:
    context_map = mapper.get_context_map(repo_path, target_file)
except RepoMapperError:
    # Continue without context
    context_map = None
```

### 5. Log at Appropriate Level
```python
import logging

# Development: DEBUG
logging.getLogger('openevolve.repo_mapper').setLevel(logging.DEBUG)

# Production: WARNING
logging.getLogger('openevolve.repo_mapper').setLevel(logging.WARNING)
```

---

## Performance Tips

### 1. Enable Parallel File Analysis
```python
config = RepoMapperConfig(max_workers=8)  # Match CPU cores
```

### 2. Reduce Token Budget for Faster Analysis
```python
config = RepoMapperConfig(
    token_budget=1000,  # Less detailed context
    max_relevant_files=5,  # Fewer files
)
```

### 3. Increase Max File Size for Large Codebases
```python
config = RepoMapperConfig(
    max_file_size_bytes=5 * 1024 * 1024,  # 5MB
)
```

### 4. Use Subdirectory Filtering
```python
# Analyze specific subdirectory only
repo_path = Path("/path/to/repo/src")  # Not root
target_file = Path("/path/to/repo/src/main.py")
```

---

For architecture details, see [repo_mapper_guide.md](repo_mapper_guide.md).
For usage examples, see [repo_mapper_demo.py](../examples/repo_mapper_demo.py).
