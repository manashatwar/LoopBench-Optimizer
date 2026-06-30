"""
Repository Context Mapper - Usage Examples and Demo

This script demonstrates various use cases of the Repository Context Mapper:
1. Basic repository analysis
2. Context map generation
3. Integration with PromptSampler
4. Cache behavior
5. Custom configuration
6. Error handling
"""

import sys
import tempfile
from pathlib import Path

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

# Add parent directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from openevolve.repo_mapper import RepoContextMapper
from openevolve.repo_mapper.models import RepoMapperConfig
from openevolve.config import PromptConfig
from openevolve.prompt.sampler import PromptSampler


def print_header(title):
    """Print section header"""
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80 + "\n")


def create_example_repository():
    """Create a small example repository for demonstration"""
    tmpdir = tempfile.mkdtemp()
    repo_path = Path(tmpdir) / "example_project"
    repo_path.mkdir()
    
    # Create main.py
    (repo_path / "main.py").write_text("""
\"\"\"Main application entry point\"\"\"
import utils
import config

def main():
    '''Run the main application'''
    settings = config.load_settings()
    data = utils.fetch_data(settings['api_url'])
    result = utils.process_data(data)
    print(f"Processed {len(result)} items")
    return result

if __name__ == '__main__':
    main()
""")
    
    # Create utils.py
    (repo_path / "utils.py").write_text("""
\"\"\"Utility functions for data processing\"\"\"
import json

def fetch_data(url):
    '''Fetch data from API'''
    # Simulated API call
    return [{"id": i, "value": i * 2} for i in range(100)]

def process_data(data):
    '''Process raw data'''
    return [item for item in data if item['value'] > 50]

def save_results(data, filename):
    '''Save results to file'''
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)
""")
    
    # Create config.py
    (repo_path / "config.py").write_text("""
\"\"\"Configuration management\"\"\"
import os

def load_settings():
    '''Load application settings'''
    return {
        'api_url': os.getenv('API_URL', 'https://api.example.com'),
        'timeout': 30,
        'retry_count': 3,
    }

def get_database_url():
    '''Get database connection string'''
    return os.getenv('DATABASE_URL', 'sqlite:///data.db')
""")
    
    # Create tests/test_utils.py
    test_dir = repo_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_utils.py").write_text("""
\"\"\"Tests for utility functions\"\"\"
import pytest
from utils import fetch_data, process_data

def test_fetch_data():
    '''Test data fetching'''
    data = fetch_data('http://test.com')
    assert len(data) == 100

def test_process_data():
    '''Test data processing'''
    raw_data = [{"id": 1, "value": 10}, {"id": 2, "value": 60}]
    processed = process_data(raw_data)
    assert len(processed) == 1
    assert processed[0]['value'] == 60
""")
    
    return repo_path


def example_1_basic_analysis():
    """Example 1: Basic repository analysis"""
    print_header("Example 1: Basic Repository Analysis")
    
    # Create example repository
    repo_path = create_example_repository()
    target_file = repo_path / "main.py"
    
    print(f"Repository: {repo_path}")
    print(f"Target file: {target_file}")
    
    # Configure with default settings
    config = RepoMapperConfig(
        enable_cache=False,  # Disable for demo
        token_budget=1500,
    )
    
    # Create mapper
    mapper = RepoContextMapper(config)
    
    # Generate context map
    print("\n🔍 Analyzing repository...")
    context_map = mapper.get_context_map(
        repo_path=repo_path,
        target_file=target_file,
    )
    
    # Display results
    print(f"\n✓ Analysis complete!")
    print(f"  - Token count: {context_map.token_count}")
    print(f"  - Relevant files: {len(context_map.relevant_files)}")
    
    print("\n📄 Target file:")
    print(f"  {context_map.target_descriptor.to_string()}")
    
    print("\n📚 Relevant files:")
    for descriptor, score in context_map.relevant_files[:3]:
        print(f"\n  {descriptor.file_path} (score: {score.total_score:.2f})")
        print(f"    Role: {descriptor.role}")
        print(f"    Functions: {', '.join(descriptor.functions[:3])}")
    
    print("\n🌳 File structure:")
    print(context_map.repository_tree)
    
    return repo_path, target_file


def example_2_context_map_generation():
    """Example 2: Generate and format context map"""
    print_header("Example 2: Context Map Generation for LLM")
    
    repo_path = create_example_repository()
    target_file = repo_path / "main.py"
    
    # Configure
    config = RepoMapperConfig(
        enable_cache=False,
        token_budget=2000,
        max_relevant_files=5,
    )
    
    mapper = RepoContextMapper(config)
    
    # Generate context map
    print("🔍 Generating context map...")
    context_map = mapper.get_context_map(repo_path, target_file)
    
    # Format for LLM prompt
    prompt_section = context_map.to_prompt_section()
    
    print("\n✓ Context map generated!")
    print(f"  - Token count: {context_map.token_count}")
    
    print("\n📝 Formatted for LLM prompt:")
    print("-" * 80)
    print(prompt_section[:800])  # Show first 800 chars
    if len(prompt_section) > 800:
        print("\n... [truncated] ...")
    print("-" * 80)


def example_3_promptsampler_integration():
    """Example 3: Integration with PromptSampler"""
    print_header("Example 3: PromptSampler Integration")
    
    repo_path = create_example_repository()
    target_file = repo_path / "main.py"
    
    # Configure PromptSampler with repo mapper
    config = PromptConfig(
        repo_mapper=RepoMapperConfig(
            enable_cache=False,
            token_budget=1500,
        )
    )
    
    # Create sampler
    sampler = PromptSampler(config)
    
    # Read target file
    code = target_file.read_text()
    
    print("🔍 Generating context-aware prompt...")
    
    # Generate prompt WITH context
    prompt_with_context = sampler.build_prompt(
        current_program=code,
        program_metrics={"performance": 0.65, "correctness": 0.90},
        language="python",
        repo_path=str(repo_path),
        target_file=str(target_file),
    )
    
    # Generate prompt WITHOUT context (for comparison)
    sampler_no_context = PromptSampler(PromptConfig())
    prompt_without_context = sampler_no_context.build_prompt(
        current_program=code,
        program_metrics={"performance": 0.65, "correctness": 0.90},
        language="python",
    )
    
    print("\n✓ Prompts generated!")
    
    print("\n❌ WITHOUT context (length):", len(prompt_without_context["user"]))
    print("✅ WITH context (length):", len(prompt_with_context["user"]))
    
    has_repo_context = "## Repository Context" in prompt_with_context["user"]
    mentions_utils = "utils.py" in prompt_with_context["user"]
    mentions_config = "config.py" in prompt_with_context["user"]
    
    print("\n🎯 Context included:")
    print(f"  - Repository Context section: {'✓' if has_repo_context else '✗'}")
    print(f"  - References utils.py: {'✓' if mentions_utils else '✗'}")
    print(f"  - References config.py: {'✓' if mentions_config else '✗'}")
    
    # Show excerpt
    if has_repo_context:
        start = prompt_with_context["user"].find("## Repository Context")
        end = prompt_with_context["user"].find("# Program Evolution", start)
        if end == -1:
            end = start + 600
        excerpt = prompt_with_context["user"][start:end]
        
        print("\n📝 Context excerpt:")
        print("-" * 80)
        print(excerpt)
        print("-" * 80)


def example_4_cache_behavior():
    """Example 4: Cache behavior demonstration"""
    print_header("Example 4: Cache Behavior")
    
    repo_path = create_example_repository()
    target_file = repo_path / "main.py"
    
    # Configure with cache enabled
    config = RepoMapperConfig(
        enable_cache=True,
        token_budget=1500,
    )
    
    mapper = RepoContextMapper(config)
    
    # First call (cache miss)
    print("🔍 First call (cache miss)...")
    import time
    start = time.time()
    context_map_1 = mapper.get_context_map(repo_path, target_file)
    time_1 = time.time() - start
    
    print(f"✓ Analysis complete in {time_1*1000:.0f}ms")
    print(f"  - Relevant files: {len(context_map_1.relevant_files)}")
    
    # Second call (cache hit)
    print("\n🔍 Second call (cache hit)...")
    start = time.time()
    context_map_2 = mapper.get_context_map(repo_path, target_file)
    time_2 = time.time() - start
    
    print(f"✓ Analysis complete in {time_2*1000:.0f}ms")
    print(f"  - Relevant files: {len(context_map_2.relevant_files)}")
    
    # Show speedup
    speedup = time_1 / time_2 if time_2 > 0 else float('inf')
    print(f"\n⚡ Cache speedup: {speedup:.1f}x faster")
    
    # Different target (uses same cached repo scan)
    print("\n🔍 Third call with different target...")
    start = time.time()
    context_map_3 = mapper.get_context_map(
        repo_path=repo_path,
        target_file=repo_path / "utils.py",
    )
    time_3 = time.time() - start
    
    print(f"✓ Analysis complete in {time_3*1000:.0f}ms")
    print(f"  - Relevant files: {len(context_map_3.relevant_files)}")
    print(f"  (Used cached repo scan, re-scored for new target)")


def example_5_custom_configuration():
    """Example 5: Custom configuration"""
    print_header("Example 5: Custom Configuration")
    
    repo_path = create_example_repository()
    target_file = repo_path / "main.py"
    
    # Aggressive configuration (small context)
    print("🔧 Configuration 1: Aggressive (small context)")
    config_aggressive = RepoMapperConfig(
        token_budget=500,  # Very limited
        max_relevant_files=2,  # Only top 2 files
        max_file_descriptor_length=50,  # Short descriptions
        enable_cache=False,
    )
    
    mapper_aggressive = RepoContextMapper(config_aggressive)
    context_aggressive = mapper_aggressive.get_context_map(repo_path, target_file)
    
    print(f"  - Token count: {context_aggressive.token_count}")
    print(f"  - Relevant files: {len(context_aggressive.relevant_files)}")
    
    # Generous configuration (large context)
    print("\n🔧 Configuration 2: Generous (large context)")
    config_generous = RepoMapperConfig(
        token_budget=5000,  # Very generous
        max_relevant_files=10,  # Many files
        max_file_descriptor_length=300,  # Detailed descriptions
        enable_cache=False,
    )
    
    mapper_generous = RepoContextMapper(config_generous)
    context_generous = mapper_generous.get_context_map(repo_path, target_file)
    
    print(f"  - Token count: {context_generous.token_count}")
    print(f"  - Relevant files: {len(context_generous.relevant_files)}")
    
    print("\n📊 Comparison:")
    print(f"  Aggressive: {context_aggressive.token_count} tokens, {len(context_aggressive.relevant_files)} files")
    print(f"  Generous:   {context_generous.token_count} tokens, {len(context_generous.relevant_files)} files")


def example_6_error_handling():
    """Example 6: Error handling"""
    print_header("Example 6: Error Handling")
    
    from openevolve.repo_mapper import RepoMapperError
    
    config = RepoMapperConfig(enable_cache=False)
    mapper = RepoContextMapper(config)
    
    # Test 1: Invalid repository path
    print("🧪 Test 1: Invalid repository path")
    try:
        invalid_path = Path("/nonexistent/repository")
        context = mapper.get_context_map(
            repo_path=invalid_path,
            target_file=invalid_path / "main.py",
        )
        print("  ✗ Should have failed!")
    except RepoMapperError as e:
        print(f"  ✓ Caught error: {type(e).__name__}")
        print(f"    Message: {e}")
    
    # Test 2: Target file not in repository
    print("\n🧪 Test 2: Target file outside repository")
    try:
        repo_path = create_example_repository()
        outside_file = Path("/tmp/outside.py")
        context = mapper.get_context_map(
            repo_path=repo_path,
            target_file=outside_file,
        )
        print("  ✗ Should have failed!")
    except (RepoMapperError, ValueError) as e:
        print(f"  ✓ Caught error: {type(e).__name__}")
        print(f"    Message: {e}")
    
    # Test 3: Graceful degradation (continues despite errors)
    print("\n🧪 Test 3: Graceful degradation")
    repo_path = create_example_repository()
    
    # Create a file that will cause parse errors
    (repo_path / "broken.py").write_text("def broken syntax is here")
    
    try:
        target_file = repo_path / "main.py"
        context = mapper.get_context_map(repo_path, target_file)
        print("  ✓ Analysis completed despite parse errors")
        print(f"    Relevant files: {len(context.relevant_files)}")
        print("    (Broken files skipped gracefully)")
    except RepoMapperError as e:
        print(f"  ✗ Unexpected failure: {e}")


def example_7_before_after_prompts():
    """Example 7: Before/After prompt comparison"""
    print_header("Example 7: Before/After Prompt Comparison")
    
    repo_path = create_example_repository()
    target_file = repo_path / "main.py"
    code = target_file.read_text()
    
    # WITHOUT context
    print("📝 Generating prompt WITHOUT repository context...")
    config_without = PromptConfig()
    sampler_without = PromptSampler(config_without)
    prompt_without = sampler_without.build_prompt(
        current_program=code,
        program_metrics={"performance": 0.60},
        language="python",
    )
    
    # WITH context
    print("📝 Generating prompt WITH repository context...")
    config_with = PromptConfig(
        repo_mapper=RepoMapperConfig(enable_cache=False, token_budget=1500)
    )
    sampler_with = PromptSampler(config_with)
    prompt_with = sampler_with.build_prompt(
        current_program=code,
        program_metrics={"performance": 0.60},
        language="python",
        repo_path=str(repo_path),
        target_file=str(target_file),
    )
    
    print("\n" + "=" * 80)
    print(" COMPARISON")
    print("=" * 80)
    
    print("\n❌ WITHOUT Context:")
    print(f"  - Length: {len(prompt_without['user'])} characters")
    print(f"  - Has Repository Context: No")
    print(f"  - LLM knows: Only main.py code")
    print(f"  - LLM doesn't know: utils.py, config.py exist or their purpose")
    
    print("\n✅ WITH Context:")
    print(f"  - Length: {len(prompt_with['user'])} characters")
    print(f"  - Has Repository Context: Yes")
    print(f"  - LLM knows: main.py + utils.py + config.py + relationships")
    print(f"  - Context overhead: {len(prompt_with['user']) - len(prompt_without['user'])} characters (~{(len(prompt_with['user']) - len(prompt_without['user'])) / 4:.0f} tokens)")
    
    print("\n💡 Value:")
    print("  - Generic prompt: 'Improve this code'")
    print("  - Context-aware: 'Improve main.py (uses utils.py for processing, config.py for settings)'")
    print("  - Result: LLM can make specific, targeted recommendations")


def main():
    """Run all examples"""
    print("\n" + "🚀" * 40)
    print("  Repository Context Mapper - Usage Examples and Demo")
    print("🚀" * 40)
    
    examples = [
        ("Basic Analysis", example_1_basic_analysis),
        ("Context Map Generation", example_2_context_map_generation),
        ("PromptSampler Integration", example_3_promptsampler_integration),
        ("Cache Behavior", example_4_cache_behavior),
        ("Custom Configuration", example_5_custom_configuration),
        ("Error Handling", example_6_error_handling),
        ("Before/After Comparison", example_7_before_after_prompts),
    ]
    
    for i, (name, example_func) in enumerate(examples, 1):
        try:
            example_func()
        except Exception as e:
            print(f"\n❌ Example {i} failed: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 80)
    print(" ✨ All examples complete!")
    print("=" * 80)
    print("\nFor more information:")
    print("  - Architecture Guide: docs/repo_mapper_guide.md")
    print("  - API Reference: docs/repo_mapper_api.md")
    print("  - Configuration: See RepoMapperConfig in openevolve/repo_mapper/models.py")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
