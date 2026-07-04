"""
Test prompt generation with repository context
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from openevolve.config import PromptConfig
from openevolve.prompt.sampler import PromptSampler
from openevolve.repo_mapper.models import RepoMapperConfig


class TestPromptWithRepoContext:
    """Test prompt generation with repository context mapper"""
    
    def test_prompt_without_repo_context(self):
        """Test backward compatibility - prompt generation without repo context"""
        config = PromptConfig()
        sampler = PromptSampler(config)
        
        prompt = sampler.build_prompt(
            current_program="def add(a, b): return a + b",
            program_metrics={"accuracy": 0.95},
            language="python",
        )
        
        assert "system" in prompt
        assert "user" in prompt
        assert "def add" in prompt["user"]
        # Should have empty repo_context
        assert "{repo_context}" not in prompt["user"]  # Should be replaced with empty string
    
    def test_prompt_with_repo_context_disabled(self):
        """Test with repo_mapper config but no paths provided"""
        config = PromptConfig(
            repo_mapper=RepoMapperConfig(enable_cache=False)
        )
        sampler = PromptSampler(config)
        
        prompt = sampler.build_prompt(
            current_program="def add(a, b): return a + b",
            program_metrics={"accuracy": 0.95},
            language="python",
            # No repo_path or target_file provided
        )
        
        assert "system" in prompt
        assert "user" in prompt
        # Should have empty repo_context
        assert "## Repository Context" not in prompt["user"]
    
    def test_prompt_with_repo_context_enabled(self):
        """Test prompt generation with repository context"""
        with TemporaryDirectory() as tmpdir:
            # Create test repository
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()
            
            # Create target file
            target_file = repo_path / "main.py"
            target_file.write_text("""
def main():
    '''Main function'''
    result = helper()
    return result
""")
            
            # Create helper file
            helper_file = repo_path / "utils.py"
            helper_file.write_text("""
def helper():
    '''Helper function'''
    return 42
""")
            
            # Configure with repo mapper
            config = PromptConfig(
                repo_mapper=RepoMapperConfig(
                    enable_cache=False,
                    token_budget=1000,
                )
            )
            sampler = PromptSampler(config)
            
            # Generate prompt with repo context
            prompt = sampler.build_prompt(
                current_program=target_file.read_text(),
                program_metrics={"performance": 0.85},
                language="python",
                repo_path=str(repo_path),
                target_file=str(target_file),
            )
            
            assert "system" in prompt
            assert "user" in prompt
            
            # Should contain repository context
            assert "## Repository Context" in prompt["user"]
            assert "Target File: main.py" in prompt["user"]
            
            # Should include utils.py as relevant file (same directory)
            assert "utils.py" in prompt["user"]
    
    def test_graceful_degradation_on_mapper_error(self):
        """Test that prompt generation continues if repo mapper fails"""
        config = PromptConfig(
            repo_mapper=RepoMapperConfig(enable_cache=False)
        )
        sampler = PromptSampler(config)
        
        # Provide invalid paths
        prompt = sampler.build_prompt(
            current_program="def add(a, b): return a + b",
            program_metrics={"accuracy": 0.95},
            language="python",
            repo_path="/nonexistent/path",
            target_file="/nonexistent/file.py",
        )
        
        # Should still generate prompt without context
        assert "system" in prompt
        assert "user" in prompt
        assert "def add" in prompt["user"]
        # Repository context should be empty due to error
        assert "## Repository Context" not in prompt["user"]
    
    def test_prompt_context_position(self):
        """Test that repo context appears in correct position (after metrics, before artifacts)"""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test_repo"
            repo_path.mkdir()
            
            target_file = repo_path / "main.py"
            target_file.write_text("def main(): pass")
            
            config = PromptConfig(
                repo_mapper=RepoMapperConfig(enable_cache=False),
                include_artifacts=True,
            )
            sampler = PromptSampler(config)
            
            prompt = sampler.build_prompt(
                current_program="def main(): pass",
                program_metrics={"score": 0.9},
                language="python",
                repo_path=str(repo_path),
                target_file=str(target_file),
                program_artifacts={"stderr": "Test error"},
            )
            
            user_message = prompt["user"]
            
            # Check ordering: metrics -> repo_context -> artifacts -> program
            metrics_pos = user_message.find("Fitness:")
            repo_context_pos = user_message.find("## Repository Context")
            artifacts_pos = user_message.find("## Execution Feedback")
            program_pos = user_message.find("# Current Program")
            
            # Repo context should appear after metrics but before artifacts/program
            assert metrics_pos < repo_context_pos
            assert metrics_pos >= 0, "Metrics section not found"
            assert repo_context_pos >= 0, "Repository Context section not found"
            # If artifacts exist, repo_context before artifacts
            if artifacts_pos > 0:
                assert repo_context_pos < artifacts_pos
            if program_pos > 0:
                assert repo_context_pos < program_pos


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
