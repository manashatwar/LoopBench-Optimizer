"""
Unit tests for repo_mapper.models module.

Tests the core data structures: RepoMapperConfig, FileNode, RepositoryMap,
ImportRelation, and ImportGraph.
"""

import time
from pathlib import Path
from openevolve.repo_mapper.models import (
    FileNode, 
    RepositoryMap, 
    RepoMapperConfig,
    ImportRelation,
    ImportGraph,
)


def test_repo_mapper_config_defaults():
    """Test RepoMapperConfig has sensible defaults."""
    config = RepoMapperConfig()
    
    # Scanning configuration
    assert isinstance(config.ignore_patterns, list)
    assert ".git" in config.ignore_patterns
    assert "__pycache__" in config.ignore_patterns
    assert config.max_traversal_depth == 10
    assert config.max_file_size_bytes == 10_000_000
    
    # Analysis configuration
    assert config.max_relevant_files == 10
    assert config.max_file_descriptor_length == 200
    assert config.parse_timeout_seconds == 5.0
    
    # Token budget
    assert config.token_budget == 2000
    assert config.estimate_tokens_per_char == 0.25
    
    # Caching
    assert config.enable_cache is True
    assert config.cache_dir is None
    assert config.cache_ttl_seconds == 3600.0
    
    # Performance
    assert config.parallel_analysis is True
    assert config.max_workers == 4


def test_repo_mapper_config_custom():
    """Test RepoMapperConfig accepts custom values."""
    config = RepoMapperConfig(
        ignore_patterns=["*.txt", "temp/"],
        max_traversal_depth=5,
        token_budget=1000,
        enable_cache=False,
    )
    
    assert config.ignore_patterns == ["*.txt", "temp/"]
    assert config.max_traversal_depth == 5
    assert config.token_budget == 1000
    assert config.enable_cache is False


def test_file_node_creation():
    """Test FileNode dataclass creation."""
    node = FileNode(
        path=Path("src/main.py"),
        absolute_path=Path("/home/user/project/src/main.py"),
        is_dir=False,
        size_bytes=1024,
        modified_time=1234567890.0,
        depth=2,
    )
    
    assert node.path == Path("src/main.py")
    assert node.absolute_path == Path("/home/user/project/src/main.py")
    assert node.is_dir is False
    assert node.size_bytes == 1024
    assert node.modified_time == 1234567890.0
    assert node.depth == 2


def test_file_node_str_representation():
    """Test FileNode string representations."""
    file_node = FileNode(
        path=Path("src/main.py"),
        absolute_path=Path("/home/user/project/src/main.py"),
        is_dir=False,
        size_bytes=1024,
        modified_time=1234567890.0,
        depth=2,
    )
    
    dir_node = FileNode(
        path=Path("src"),
        absolute_path=Path("/home/user/project/src"),
        is_dir=True,
        size_bytes=0,
        modified_time=1234567890.0,
        depth=1,
    )
    
    assert "file:" in str(file_node)
    assert "src/main.py" in str(file_node) or "src\\main.py" in str(file_node)
    
    assert "dir:" in str(dir_node)
    assert "src" in str(dir_node)


def test_repository_map_creation():
    """Test RepositoryMap creation with file nodes."""
    root = FileNode(
        path=Path("."),
        absolute_path=Path("/home/user/project"),
        is_dir=True,
        size_bytes=0,
        modified_time=1234567890.0,
        depth=0,
    )
    
    files = {
        Path("src"): FileNode(
            path=Path("src"),
            absolute_path=Path("/home/user/project/src"),
            is_dir=True,
            size_bytes=0,
            modified_time=1234567890.0,
            depth=1,
        ),
        Path("src/main.py"): FileNode(
            path=Path("src/main.py"),
            absolute_path=Path("/home/user/project/src/main.py"),
            is_dir=False,
            size_bytes=1024,
            modified_time=1234567890.0,
            depth=2,
        ),
    }
    
    repo_map = RepositoryMap(
        repo_path=Path("/home/user/project"),
        root_node=root,
        files=files,
        scan_timestamp=time.time(),
    )
    
    assert repo_map.repo_path == Path("/home/user/project")
    assert repo_map.root_node == root
    assert len(repo_map.files) == 2
    assert Path("src/main.py") in repo_map.files


def test_repository_map_to_tree_string():
    """Test RepositoryMap.to_tree_string() generates correct tree format."""
    root = FileNode(
        path=Path("project"),
        absolute_path=Path("/home/user/project"),
        is_dir=True,
        size_bytes=0,
        modified_time=1234567890.0,
        depth=0,
    )
    
    files = {
        Path("src"): FileNode(
            path=Path("src"),
            absolute_path=Path("/home/user/project/src"),
            is_dir=True,
            size_bytes=0,
            modified_time=1234567890.0,
            depth=1,
        ),
        Path("src/main.py"): FileNode(
            path=Path("src/main.py"),
            absolute_path=Path("/home/user/project/src/main.py"),
            is_dir=False,
            size_bytes=1024,
            modified_time=1234567890.0,
            depth=2,
        ),
        Path("README.md"): FileNode(
            path=Path("README.md"),
            absolute_path=Path("/home/user/project/README.md"),
            is_dir=False,
            size_bytes=512,
            modified_time=1234567890.0,
            depth=1,
        ),
    }
    
    repo_map = RepositoryMap(
        repo_path=Path("/home/user/project"),
        root_node=root,
        files=files,
        scan_timestamp=time.time(),
    )
    
    tree_str = repo_map.to_tree_string()
    
    # Verify tree structure
    assert "project/" in tree_str
    assert "src/" in tree_str
    assert "main.py" in tree_str
    assert "README.md" in tree_str
    
    # Verify indentation (src should be indented more than root)
    lines = tree_str.split("\n")
    assert len(lines) >= 4


def test_repository_map_to_tree_string_with_max_depth():
    """Test RepositoryMap.to_tree_string() respects max_depth parameter."""
    root = FileNode(
        path=Path("project"),
        absolute_path=Path("/home/user/project"),
        is_dir=True,
        size_bytes=0,
        modified_time=1234567890.0,
        depth=0,
    )
    
    files = {
        Path("src"): FileNode(
            path=Path("src"),
            absolute_path=Path("/home/user/project/src"),
            is_dir=True,
            size_bytes=0,
            modified_time=1234567890.0,
            depth=1,
        ),
        Path("src/main.py"): FileNode(
            path=Path("src/main.py"),
            absolute_path=Path("/home/user/project/src/main.py"),
            is_dir=False,
            size_bytes=1024,
            modified_time=1234567890.0,
            depth=2,
        ),
    }
    
    repo_map = RepositoryMap(
        repo_path=Path("/home/user/project"),
        root_node=root,
        files=files,
        scan_timestamp=time.time(),
    )
    
    # With max_depth=1, should only show root and src, not main.py
    tree_str = repo_map.to_tree_string(max_depth=1)
    
    assert "src/" in tree_str
    assert "main.py" not in tree_str


def test_repository_map_get_file():
    """Test RepositoryMap.get_file() retrieves nodes by path."""
    root = FileNode(
        path=Path("."),
        absolute_path=Path("/home/user/project"),
        is_dir=True,
        size_bytes=0,
        modified_time=1234567890.0,
        depth=0,
    )
    
    main_py = FileNode(
        path=Path("src/main.py"),
        absolute_path=Path("/home/user/project/src/main.py"),
        is_dir=False,
        size_bytes=1024,
        modified_time=1234567890.0,
        depth=2,
    )
    
    files = {Path("src/main.py"): main_py}
    
    repo_map = RepositoryMap(
        repo_path=Path("/home/user/project"),
        root_node=root,
        files=files,
        scan_timestamp=time.time(),
    )
    
    # Should find existing file
    found = repo_map.get_file(Path("src/main.py"))
    assert found == main_py
    
    # Should return None for non-existent file
    not_found = repo_map.get_file(Path("nonexistent.py"))
    assert not_found is None


def test_repository_map_get_files_in_directory():
    """Test RepositoryMap.get_files_in_directory() finds direct children."""
    root = FileNode(
        path=Path("."),
        absolute_path=Path("/home/user/project"),
        is_dir=True,
        size_bytes=0,
        modified_time=1234567890.0,
        depth=0,
    )
    
    files = {
        Path("src"): FileNode(
            path=Path("src"),
            absolute_path=Path("/home/user/project/src"),
            is_dir=True,
            size_bytes=0,
            modified_time=1234567890.0,
            depth=1,
        ),
        Path("src/main.py"): FileNode(
            path=Path("src/main.py"),
            absolute_path=Path("/home/user/project/src/main.py"),
            is_dir=False,
            size_bytes=1024,
            modified_time=1234567890.0,
            depth=2,
        ),
        Path("src/utils.py"): FileNode(
            path=Path("src/utils.py"),
            absolute_path=Path("/home/user/project/src/utils.py"),
            is_dir=False,
            size_bytes=512,
            modified_time=1234567890.0,
            depth=2,
        ),
        Path("README.md"): FileNode(
            path=Path("README.md"),
            absolute_path=Path("/home/user/project/README.md"),
            is_dir=False,
            size_bytes=256,
            modified_time=1234567890.0,
            depth=1,
        ),
    }
    
    repo_map = RepositoryMap(
        repo_path=Path("/home/user/project"),
        root_node=root,
        files=files,
        scan_timestamp=time.time(),
    )
    
    # Get files in src directory
    src_files = repo_map.get_files_in_directory(Path("src"))
    assert len(src_files) == 2
    
    file_names = {f.path.name for f in src_files}
    assert "main.py" in file_names
    assert "utils.py" in file_names


# ============================================================================
# ImportRelation Tests
# ============================================================================

def test_import_relation_creation():
    """Test ImportRelation dataclass creation with all fields."""
    relation = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    assert relation.source_file == Path("src/main.py")
    assert relation.target_module == "openevolve.config"
    assert relation.target_file == Path("openevolve/config.py")
    assert relation.import_type == "absolute"
    assert relation.line_number == 5


def test_import_relation_with_none_target():
    """Test ImportRelation with unresolved target (external/third-party)."""
    relation = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="numpy",
        target_file=None,
        import_type="third_party",
        line_number=3,
    )
    
    assert relation.source_file == Path("src/main.py")
    assert relation.target_module == "numpy"
    assert relation.target_file is None
    assert relation.import_type == "third_party"
    assert relation.line_number == 3


def test_import_relation_str_representation():
    """Test ImportRelation string representation."""
    # With resolved target
    relation1 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    str_repr = str(relation1)
    assert "src/main.py" in str_repr or "src\\main.py" in str_repr
    assert "openevolve/config.py" in str_repr or "openevolve\\config.py" in str_repr
    assert "line 5" in str_repr
    
    # With unresolved target
    relation2 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="numpy",
        target_file=None,
        import_type="third_party",
        line_number=3,
    )
    
    str_repr2 = str(relation2)
    assert "src/main.py" in str_repr2 or "src\\main.py" in str_repr2
    assert "numpy" in str_repr2
    assert "line 3" in str_repr2


def test_import_relation_repr():
    """Test ImportRelation detailed repr()."""
    relation = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    repr_str = repr(relation)
    assert "ImportRelation" in repr_str
    assert "src/main.py" in repr_str or "src\\main.py" in repr_str
    assert "openevolve.config" in repr_str
    assert "absolute" in repr_str
    assert "line=5" in repr_str


# ============================================================================
# ImportGraph Tests
# ============================================================================

def test_import_graph_creation_empty():
    """Test creating an empty ImportGraph."""
    graph = ImportGraph()
    
    assert len(graph.relations) == 0
    assert len(graph.adjacency) == 0
    assert len(graph.reverse_adjacency) == 0
    assert len(graph) == 0


def test_import_graph_add_relation():
    """Test adding a single relation to the graph."""
    graph = ImportGraph()
    
    relation = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    graph.add_relation(relation)
    
    # Check relation added
    assert len(graph.relations) == 1
    assert graph.relations[0] == relation
    
    # Check adjacency updated
    assert Path("src/main.py") in graph.adjacency
    assert Path("openevolve/config.py") in graph.adjacency[Path("src/main.py")]
    
    # Check reverse adjacency updated
    assert Path("openevolve/config.py") in graph.reverse_adjacency
    assert Path("src/main.py") in graph.reverse_adjacency[Path("openevolve/config.py")]


def test_import_graph_add_relation_with_none_target():
    """Test adding relation with None target (external import)."""
    graph = ImportGraph()
    
    relation = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="numpy",
        target_file=None,
        import_type="third_party",
        line_number=3,
    )
    
    graph.add_relation(relation)
    
    # Relation added to list
    assert len(graph.relations) == 1
    
    # But adjacency NOT updated (target is None)
    assert Path("src/main.py") not in graph.adjacency
    assert len(graph.adjacency) == 0
    assert len(graph.reverse_adjacency) == 0


def test_import_graph_add_multiple_relations():
    """Test adding multiple relations from same source file."""
    graph = ImportGraph()
    
    relation1 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    relation2 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.utils",
        target_file=Path("openevolve/utils/__init__.py"),
        import_type="absolute",
        line_number=6,
    )
    
    graph.add_relation(relation1)
    graph.add_relation(relation2)
    
    # Check both relations added
    assert len(graph.relations) == 2
    
    # Check adjacency contains both targets
    assert len(graph.adjacency[Path("src/main.py")]) == 2
    assert Path("openevolve/config.py") in graph.adjacency[Path("src/main.py")]
    assert Path("openevolve/utils/__init__.py") in graph.adjacency[Path("src/main.py")]
    
    # Check reverse adjacency
    assert Path("src/main.py") in graph.reverse_adjacency[Path("openevolve/config.py")]
    assert Path("src/main.py") in graph.reverse_adjacency[Path("openevolve/utils/__init__.py")]


def test_import_graph_get_direct_imports():
    """Test get_direct_imports() returns files imported by a file."""
    graph = ImportGraph()
    
    relation1 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    relation2 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.utils",
        target_file=Path("openevolve/utils/__init__.py"),
        import_type="absolute",
        line_number=6,
    )
    
    graph.add_relation(relation1)
    graph.add_relation(relation2)
    
    # Get direct imports
    imports = graph.get_direct_imports(Path("src/main.py"))
    
    assert len(imports) == 2
    assert Path("openevolve/config.py") in imports
    assert Path("openevolve/utils/__init__.py") in imports


def test_import_graph_get_direct_imports_empty():
    """Test get_direct_imports() returns empty set for file with no imports."""
    graph = ImportGraph()
    
    # File not in graph
    imports = graph.get_direct_imports(Path("src/isolated.py"))
    assert len(imports) == 0
    assert isinstance(imports, set)


def test_import_graph_get_reverse_imports():
    """Test get_reverse_imports() returns files that import a file."""
    graph = ImportGraph()
    
    relation1 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    relation2 = ImportRelation(
        source_file=Path("src/utils.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=3,
    )
    
    graph.add_relation(relation1)
    graph.add_relation(relation2)
    
    # Get reverse imports (who imports config.py?)
    reverse_imports = graph.get_reverse_imports(Path("openevolve/config.py"))
    
    assert len(reverse_imports) == 2
    assert Path("src/main.py") in reverse_imports
    assert Path("src/utils.py") in reverse_imports


def test_import_graph_get_reverse_imports_empty():
    """Test get_reverse_imports() returns empty set for file not imported."""
    graph = ImportGraph()
    
    # File not in graph
    reverse_imports = graph.get_reverse_imports(Path("src/isolated.py"))
    assert len(reverse_imports) == 0
    assert isinstance(reverse_imports, set)


def test_import_graph_get_all_files():
    """Test get_all_files() returns all files in the graph."""
    graph = ImportGraph()
    
    relation1 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    relation2 = ImportRelation(
        source_file=Path("src/utils.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=3,
    )
    
    relation3 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="src.utils",
        target_file=Path("src/utils.py"),
        import_type="relative",
        line_number=7,
    )
    
    graph.add_relation(relation1)
    graph.add_relation(relation2)
    graph.add_relation(relation3)
    
    # Get all files
    all_files = graph.get_all_files()
    
    assert len(all_files) == 3
    assert Path("src/main.py") in all_files
    assert Path("src/utils.py") in all_files
    assert Path("openevolve/config.py") in all_files


def test_import_graph_has_file():
    """Test has_file() checks if a file is in the graph."""
    graph = ImportGraph()
    
    relation = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    graph.add_relation(relation)
    
    # Files in graph
    assert graph.has_file(Path("src/main.py")) is True
    assert graph.has_file(Path("openevolve/config.py")) is True
    
    # File not in graph
    assert graph.has_file(Path("src/isolated.py")) is False


def test_import_graph_len():
    """Test len() returns number of relations."""
    graph = ImportGraph()
    
    assert len(graph) == 0
    
    relation1 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    graph.add_relation(relation1)
    assert len(graph) == 1
    
    relation2 = ImportRelation(
        source_file=Path("src/utils.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=3,
    )
    
    graph.add_relation(relation2)
    assert len(graph) == 2


def test_import_graph_repr():
    """Test ImportGraph repr() shows statistics."""
    graph = ImportGraph()
    
    relation1 = ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=5,
    )
    
    relation2 = ImportRelation(
        source_file=Path("src/utils.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=3,
    )
    
    graph.add_relation(relation1)
    graph.add_relation(relation2)
    
    repr_str = repr(graph)
    assert "ImportGraph" in repr_str
    assert "relations=2" in repr_str
    assert "files=3" in repr_str  # main.py, utils.py, config.py


def test_import_graph_complex_scenario():
    """Test complex import graph with multiple interconnected files."""
    graph = ImportGraph()
    
    # main.py imports config.py and utils.py
    graph.add_relation(ImportRelation(
        source_file=Path("src/main.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=1,
    ))
    graph.add_relation(ImportRelation(
        source_file=Path("src/main.py"),
        target_module="src.utils",
        target_file=Path("src/utils.py"),
        import_type="relative",
        line_number=2,
    ))
    
    # utils.py imports config.py
    graph.add_relation(ImportRelation(
        source_file=Path("src/utils.py"),
        target_module="openevolve.config",
        target_file=Path("openevolve/config.py"),
        import_type="absolute",
        line_number=1,
    ))
    
    # utils.py imports external library
    graph.add_relation(ImportRelation(
        source_file=Path("src/utils.py"),
        target_module="numpy",
        target_file=None,
        import_type="third_party",
        line_number=2,
    ))
    
    # Verify structure
    assert len(graph) == 4
    assert len(graph.get_all_files()) == 3  # main, utils, config (numpy not counted)
    
    # main.py imports
    main_imports = graph.get_direct_imports(Path("src/main.py"))
    assert len(main_imports) == 2
    assert Path("openevolve/config.py") in main_imports
    assert Path("src/utils.py") in main_imports
    
    # config.py is imported by
    config_importers = graph.get_reverse_imports(Path("openevolve/config.py"))
    assert len(config_importers) == 2
    assert Path("src/main.py") in config_importers
    assert Path("src/utils.py") in config_importers
    
    # utils.py imports (only internal ones counted)
    utils_imports = graph.get_direct_imports(Path("src/utils.py"))
    assert len(utils_imports) == 1
    assert Path("openevolve/config.py") in utils_imports
