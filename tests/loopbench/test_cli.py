"""
Tests for loopbench CLI commands.
"""
import unittest
import tempfile
import os
import unittest
import tempfile
import os
import sys
import argparse
from pathlib import Path

from loopbench.cli import _cmd_check

class TestCLICheck(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        
        # Save original stdout encoding/errors or reconfigure to utf-8 to avoid Windows Unicode issues
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass
        if hasattr(sys.stderr, "reconfigure"):
            try:
                sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass
        
    def tearDown(self):
        self.temp_dir.cleanup()
        
    def test_cmd_check_non_python_evaluator(self):
        # Create evaluator and program files
        evaluator_path = self.base_path / "evaluator.txt"  # Not a python file
        program_path = self.base_path / "program.py"
        
        evaluator_path.write_text("not python code")
        program_path.write_text("print('hello')")
        
        config_content = f"""
target:
  program: {program_path.name}
  evaluator: {evaluator_path.name}
"""
        config_path = self.base_path / "loopbench.yaml"
        config_path.write_text(config_content)
        
        args = argparse.Namespace(config=str(config_path))
        
        # This should handle the None spec/loader gracefully and return 1
        res = _cmd_check(args)
        self.assertEqual(res, 1)

    def test_cmd_check_valid_evaluator(self):
        evaluator_path = self.base_path / "evaluator.py"
        program_path = self.base_path / "program.py"
        
        evaluator_path.write_text("""
class MockResult:
    def __init__(self):
        self.metrics = {"score": 0.95}

def evaluate(program_path):
    return MockResult()
""")
        program_path.write_text("print('hello')")
        
        config_content = f"""
target:
  program: {program_path.name}
  evaluator: {evaluator_path.name}
"""
        config_path = self.base_path / "loopbench.yaml"
        config_path.write_text(config_content)
        
        args = argparse.Namespace(config=str(config_path))
        
        res = _cmd_check(args)
        self.assertEqual(res, 0)

    def test_cmd_check_no_evaluate_function(self):
        evaluator_path = self.base_path / "evaluator.py"
        program_path = self.base_path / "program.py"
        
        # Missing evaluate function
        evaluator_path.write_text("""
def run_eval(program_path):
    pass
""")
        program_path.write_text("print('hello')")
        
        config_content = f"""
target:
  program: {program_path.name}
  evaluator: {evaluator_path.name}
"""
        config_path = self.base_path / "loopbench.yaml"
        config_path.write_text(config_content)
        
        args = argparse.Namespace(config=str(config_path))
        
        res = _cmd_check(args)
        self.assertEqual(res, 1)

    def test_cmd_check_evaluator_returns_dict(self):
        evaluator_path = self.base_path / "evaluator.py"
        program_path = self.base_path / "program.py"
        
        evaluator_path.write_text("""
def evaluate(program_path):
    return {"accuracy": 0.88, "latency": 1.2}
""")
        program_path.write_text("print('hello')")
        
        config_content = f"""
target:
  program: {program_path.name}
  evaluator: {evaluator_path.name}
"""
        config_path = self.base_path / "loopbench.yaml"
        config_path.write_text(config_content)
        
        args = argparse.Namespace(config=str(config_path))
        
        res = _cmd_check(args)
        self.assertEqual(res, 0)

    def test_cmd_check_evaluator_returns_none(self):
        evaluator_path = self.base_path / "evaluator.py"
        program_path = self.base_path / "program.py"
        
        evaluator_path.write_text("""
def evaluate(program_path):
    return None
""")
        program_path.write_text("print('hello')")
        
        config_content = f"""
target:
  program: {program_path.name}
  evaluator: {evaluator_path.name}
"""
        config_path = self.base_path / "loopbench.yaml"
        config_path.write_text(config_content)
        
        args = argparse.Namespace(config=str(config_path))
        
        res = _cmd_check(args)
        self.assertEqual(res, 1)

    def test_cmd_check_evaluator_returns_invalid_type(self):
        evaluator_path = self.base_path / "evaluator.py"
        program_path = self.base_path / "program.py"
        
        evaluator_path.write_text("""
def evaluate(program_path):
    return "invalid_string_result"
""")
        program_path.write_text("print('hello')")
        
        config_content = f"""
target:
  program: {program_path.name}
  evaluator: {evaluator_path.name}
"""
        config_path = self.base_path / "loopbench.yaml"
        config_path.write_text(config_content)
        
        args = argparse.Namespace(config=str(config_path))
        
        res = _cmd_check(args)
        self.assertEqual(res, 1)


from unittest.mock import MagicMock, patch

class TestCLIRun(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        
    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("openevolve.controller.OpenEvolve")
    @patch("openevolve.config.load_config")
    def test_cmd_run_best_is_none(self, mock_load_config, mock_openevolve):
        # Setup files
        evaluator_path = self.base_path / "evaluator.py"
        program_path = self.base_path / "program.py"
        evaluator_path.write_text("def evaluate(p): pass")
        program_path.write_text("print('hello')")
        
        config_content = f"""
target:
  program: {program_path.name}
  evaluator: {evaluator_path.name}
metric:
  name: combined_score
"""
        config_path = self.base_path / "loopbench.yaml"
        config_path.write_text(config_content)
        
        # Configure Mock
        mock_runner = MagicMock()
        # Make the async run method return None
        async def mock_run(*args, **kwargs):
            return None
        mock_runner.run = mock_run
        mock_openevolve.return_value = mock_runner
        
        mock_config = MagicMock()
        mock_config.max_iterations = 5
        mock_load_config.return_value = mock_config
        
        from loopbench.cli import _cmd_run
        args = argparse.Namespace(
            config=str(config_path),
            iterations=None,
            target_score=None,
            output=None,
            log_level="INFO"
        )
        
        res = _cmd_run(args)
        self.assertEqual(res, 1)


if __name__ == '__main__':
    unittest.main()
