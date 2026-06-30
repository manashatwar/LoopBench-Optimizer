"""
Task 7.4 — Integration tests for the OpenEvolve / WorkspaceManager workflow.

Covers:
- evaluate_program() writes to workspace_path when provided (7.1)
- evaluate_program() falls back to temp file when workspace_path=None (7.1)
- run_iteration_with_shared_db() uses WorkspaceManager when config provided (7.2)
- run_iteration_with_shared_db() works without workspace_manager_config (7.2)
- run_in_sandbox() builds correct docker cmd with worktree_path (7.3)
- run_in_sandbox() falls back to temp-copy behaviour without worktree_path (7.3)
- Main workspace remains unchanged after worktree evaluation (isolation check)
- Concurrent worktree creation produces unique paths (9.2 basic check)
"""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call
import tempfile

import pytest

from openevolve.workspace_manager import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_git_repo(tmp_path) -> Path:
    """Create a minimal real Git repository with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _make_evaluator_mock(metrics=None):
    """Return a mock Evaluator with evaluate_program returning given metrics."""
    metrics = metrics or {"score": 1.0}
    ev = MagicMock()
    ev.evaluate_program = AsyncMock(return_value=metrics)
    ev.get_pending_artifacts = Mock(return_value=None)
    ev.config = SimpleNamespace(
        cascade_evaluation=False,
        use_llm_feedback=False,
        max_retries=0,
        timeout=30,
        parallel_evaluations=1,
        llm_feedback_weight=0.3,
    )
    ev.program_suffix = ".py"
    ev._pending_artifacts = {}
    return ev


# ---------------------------------------------------------------------------
# Task 7.1 — evaluate_program() with workspace_path
# ---------------------------------------------------------------------------

class TestEvaluateProgramWorkspacePath:
    """Test that evaluate_program() respects the workspace_path parameter."""

    @pytest.mark.asyncio
    async def test_writes_file_to_workspace_path(self, tmp_path):
        """Program file should appear in workspace_path when provided."""
        from openevolve.evaluator import Evaluator

        ws_dir = tmp_path / "worktree"
        ws_dir.mkdir()

        captured = {}  # capture what the evaluator sees

        def _capturing_eval(program_path):
            # Record whether the file is inside ws_dir and read its content
            p = Path(program_path)
            captured["path"] = program_path
            captured["in_ws"] = str(p.parent.resolve()) == str(ws_dir.resolve())
            captured["content"] = p.read_text()
            return {"score": 1.0}

        ev = Evaluator.__new__(Evaluator)
        ev.config = SimpleNamespace(
            cascade_evaluation=False,
            use_llm_feedback=False,
            max_retries=0,
            timeout=30,
            parallel_evaluations=1,
            llm_feedback_weight=0.3,
        )
        ev.program_suffix = ".py"
        ev._pending_artifacts = {}
        ev.llm_ensemble = None
        ev.task_pool = MagicMock()
        ev.evaluate_function = _capturing_eval

        result = await ev.evaluate_program(
            "x = 1\n",
            program_id="test123",
            workspace_path=str(ws_dir),
        )

        assert result == {"score": 1.0}
        assert captured.get("in_ws"), "Evaluator should receive a path inside ws_dir"
        assert captured.get("content") == "x = 1\n"

    @pytest.mark.asyncio
    async def test_no_workspace_path_uses_tempfile(self, tmp_path):
        """Without workspace_path the original tempfile behaviour runs."""
        from openevolve.evaluator import Evaluator

        captured_paths = []

        def _evaluate(program_path):
            captured_paths.append(program_path)
            return {"score": 0.5}

        ev = Evaluator.__new__(Evaluator)
        ev.config = SimpleNamespace(
            cascade_evaluation=False,
            use_llm_feedback=False,
            max_retries=0,
            timeout=30,
            parallel_evaluations=1,
            llm_feedback_weight=0.3,
        )
        ev.program_suffix = ".py"
        ev._pending_artifacts = {}
        ev.llm_ensemble = None
        ev.prompt_sampler = None
        ev.database = None
        ev.task_pool = MagicMock()
        ev.evaluate_function = _evaluate

        result = await ev.evaluate_program("y = 2\n", program_id="pid")

        assert result == {"score": 0.5}
        # Path should be a system temp file, not inside tmp_path
        assert len(captured_paths) == 1
        assert "pid" not in captured_paths[0]  # temp file uses random name

    @pytest.mark.asyncio
    async def test_workspace_path_creates_dirs_if_missing(self, tmp_path):
        """workspace_path directories are created automatically."""
        from openevolve.evaluator import Evaluator

        deep_ws = tmp_path / "a" / "b" / "worktree"
        # Do NOT create it — evaluate_program should create it

        ev = Evaluator.__new__(Evaluator)
        ev.config = SimpleNamespace(
            cascade_evaluation=False,
            use_llm_feedback=False,
            max_retries=0,
            timeout=30,
            parallel_evaluations=1,
            llm_feedback_weight=0.3,
        )
        ev.program_suffix = ".py"
        ev._pending_artifacts = {}
        ev.llm_ensemble = None
        ev.task_pool = MagicMock()
        ev.evaluate_function = lambda p: {"score": 0.0}

        await ev.evaluate_program("z = 3\n", program_id="p1", workspace_path=str(deep_ws))

        assert deep_ws.exists()


# ---------------------------------------------------------------------------
# Task 7.2 — run_iteration_with_shared_db() integration
# ---------------------------------------------------------------------------

class TestIterationWithWorkspace:
    """Test run_iteration_with_shared_db() with workspace_manager_config."""

    def _make_config(self):
        cfg = SimpleNamespace(
            prompt=SimpleNamespace(
                num_top_programs=3,
                programs_as_changes_description=False,
                diff_summary_max_line_len=80,
                diff_summary_max_lines=10,
            ),
            diff_based_evolution=False,
            language="python",
            max_code_length=100_000,
            log_prompts=False,
            target_file=None,
        )
        return cfg

    def _make_db(self):
        parent = SimpleNamespace(
            id="parent_id",
            code="def solution(): return 1",
            metrics={"score": 0.5},
            generation=0,
            changes_description=None,
            metadata={"island": 0},
        )
        db = MagicMock()
        db.sample = Mock(return_value=(parent, []))
        db.get_artifacts = Mock(return_value=None)
        db.get_top_programs = Mock(return_value=[])
        db.current_island = 0
        db.config = SimpleNamespace(log_prompts=False, feature_dimensions=None)
        return db

    @pytest.mark.asyncio
    async def test_iteration_without_workspace_config(self):
        """Default path (no workspace_manager_config) still works."""
        from openevolve.iteration import run_iteration_with_shared_db

        ev = _make_evaluator_mock()
        llm = MagicMock()
        llm.generate_with_context = AsyncMock(return_value="```python\ndef solution(): return 2\n```")
        ps = MagicMock()
        ps.build_prompt = Mock(return_value={"system": "sys", "user": "usr"})

        result = await run_iteration_with_shared_db(
            iteration=0,
            config=self._make_config(),
            database=self._make_db(),
            evaluator=ev,
            llm_ensemble=llm,
            prompt_sampler=ps,
            workspace_manager_config=None,
        )

        # evaluate_program called WITHOUT workspace_path kwarg
        ev.evaluate_program.assert_called_once()
        call_kwargs = ev.evaluate_program.call_args
        assert "workspace_path" not in (call_kwargs.kwargs or {})

    @pytest.mark.asyncio
    async def test_iteration_with_workspace_config_uses_worktree(self, tmp_path):
        """With workspace_manager_config, evaluate_program receives workspace_path."""
        from openevolve.iteration import run_iteration_with_shared_db

        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        ev = _make_evaluator_mock()
        llm = MagicMock()
        llm.generate_with_context = AsyncMock(return_value="```python\ndef solution(): return 3\n```")
        ps = MagicMock()
        ps.build_prompt = Mock(return_value={"system": "sys", "user": "usr"})

        ws_cfg = {
            "repo_root": str(repo),
            "worktree_parent_dir": str(wt_parent),
        }

        result = await run_iteration_with_shared_db(
            iteration=0,
            config=self._make_config(),
            database=self._make_db(),
            evaluator=ev,
            llm_ensemble=llm,
            prompt_sampler=ps,
            workspace_manager_config=ws_cfg,
        )

        # evaluate_program must have been called with a workspace_path keyword
        ev.evaluate_program.assert_called_once()
        kwargs = ev.evaluate_program.call_args.kwargs
        assert "workspace_path" in kwargs
        wt_path = kwargs["workspace_path"]
        assert wt_path is not None
        assert str(wt_parent) in wt_path

    @pytest.mark.asyncio
    async def test_worktree_cleaned_up_after_iteration(self, tmp_path):
        """The worktree directory must not exist after iteration completes."""
        from openevolve.iteration import run_iteration_with_shared_db

        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        captured_wt = {}

        async def _fake_eval(code, pid, workspace_path=None):
            captured_wt["path"] = workspace_path
            assert workspace_path and Path(workspace_path).exists(), \
                "Worktree must exist during evaluation"
            return {"score": 1.0}

        ev = _make_evaluator_mock()
        ev.evaluate_program = _fake_eval

        llm = MagicMock()
        llm.generate_with_context = AsyncMock(return_value="```python\ndef solution(): return 4\n```")
        ps = MagicMock()
        ps.build_prompt = Mock(return_value={"system": "sys", "user": "usr"})

        ws_cfg = {"repo_root": str(repo), "worktree_parent_dir": str(wt_parent)}
        await run_iteration_with_shared_db(
            iteration=0,
            config=self._make_config(),
            database=self._make_db(),
            evaluator=ev,
            llm_ensemble=llm,
            prompt_sampler=ps,
            workspace_manager_config=ws_cfg,
        )

        # After context exit, the worktree must be gone
        wt_path = captured_wt.get("path")
        assert wt_path is not None
        assert not Path(wt_path).exists(), "Worktree should be cleaned up after iteration"


# ---------------------------------------------------------------------------
# Task 7.3 — run_in_sandbox() with worktree_path
# ---------------------------------------------------------------------------

class TestSandboxWithWorktreePath:
    """Test run_in_sandbox() worktree_path parameter (Docker mocked)."""

    def test_worktree_path_mounts_worktree_directly(self, tmp_path):
        """When worktree_path is given, docker cmd should mount it as /workspace."""
        from sandbox.runner import run_in_sandbox

        # Create fake worktree with program and test file
        wt = tmp_path / "worktree"
        wt.mkdir()
        prog = wt / "program.py"
        prog.write_text("x = 1")
        test_f = wt / "test_prog.py"
        test_f.write_text("def test_x(): pass")

        # Fake score.json
        fake_score = {
            "passed": 1, "failed": 0, "errors": 0, "total": 1,
            "speed_ms": 10, "correctness": 1.0, "speed_score": 1.0,
            "combined_score": 1.0, "all_passed": True,
        }

        captured_cmds = []

        def _fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            # Simulate score.json being written
            for arg in cmd:
                if "/results" in str(arg):
                    results_dir = str(arg).replace(":/results", "")
                    import json, os
                    os.makedirs(results_dir, exist_ok=True)
                    with open(f"{results_dir}/score.json", "w") as f:
                        json.dump(fake_score, f)
                    break
            return Mock(returncode=0, stdout="", stderr="")

        with patch("sandbox.runner.build_sandbox_image", return_value=True), \
             patch("subprocess.run", side_effect=_fake_run):
            result = run_in_sandbox(
                program_path=str(prog),
                test_file=str(test_f),
                worktree_path=str(wt),
            )

        # Verify the docker command mounts the worktree, not a temp copy
        docker_calls = [c for c in captured_cmds if c and c[0] == "docker"]
        assert docker_calls, "Docker should have been called"
        docker_args = " ".join(docker_calls[0])
        assert str(wt.resolve()) in docker_args
        assert ":/workspace:ro" in docker_args

    def test_without_worktree_path_uses_temp_copy(self, tmp_path):
        """Without worktree_path, the original temp-copy path is taken."""
        from sandbox.runner import run_in_sandbox

        prog = tmp_path / "program.py"
        prog.write_text("x = 1")
        test_f = tmp_path / "test_prog.py"
        test_f.write_text("def test_x(): pass")

        fake_score = {"passed": 1, "combined_score": 1.0, "all_passed": True}

        def _fake_run(cmd, **kwargs):
            # Find the results dir from the -v mounts: look for :/results suffix
            # On Windows paths contain ':', so match the LAST colon before :/results
            import json
            for arg in cmd:
                arg_s = str(arg)
                if arg_s.endswith(":/results"):
                    host_results = arg_s[: -len(":/results")]
                    Path(host_results).mkdir(parents=True, exist_ok=True)
                    (Path(host_results) / "score.json").write_text(json.dumps(fake_score))
                    break
            return Mock(returncode=0, stdout="", stderr="")

        with patch("sandbox.runner.build_sandbox_image", return_value=True), \
             patch("subprocess.run", side_effect=_fake_run):
            result = run_in_sandbox(
                program_path=str(prog),
                test_file=str(test_f),
                worktree_path=None,
            )

        assert result.get("all_passed") is True

    def test_worktree_path_param_exists(self):
        """run_in_sandbox() must accept worktree_path keyword argument."""
        import inspect
        from sandbox.runner import run_in_sandbox
        sig = inspect.signature(run_in_sandbox)
        assert "worktree_path" in sig.parameters


# ---------------------------------------------------------------------------
# Main workspace isolation check
# ---------------------------------------------------------------------------

class TestMainWorkspaceIsolation:
    """Verify the main workspace is not mutated by worktree operations."""

    def test_main_workspace_files_unchanged(self, tmp_path):
        """Files in the main repo must remain identical after worktree usage."""
        repo = _make_git_repo(tmp_path)
        original_files = {
            p.name: p.read_bytes()
            for p in repo.iterdir()
            if p.is_file()
        }

        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        with WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        ) as wt_path:
            # Write a candidate file inside the worktree
            (Path(wt_path) / "candidate.py").write_text("def f(): return 42")
            # The main repo must NOT have this file
            assert not (repo / "candidate.py").exists()

        # After cleanup, verify repo is still clean
        current_files = {
            p.name: p.read_bytes()
            for p in repo.iterdir()
            if p.is_file()
        }
        assert original_files == current_files

    def test_multiple_sequential_worktrees_unique_paths(self, tmp_path):
        """Sequential worktrees should have unique paths."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        paths = []
        for _ in range(3):
            with WorkspaceManager(
                repo_root=str(repo),
                worktree_parent_dir=str(wt_parent),
            ) as wt_path:
                paths.append(wt_path)

        # All 3 paths should be distinct
        assert len(set(paths)) == 3, "Sequential worktrees must have unique paths"
        # All should be cleaned up
        for p in paths:
            assert not Path(p).exists(), f"{p} should be cleaned up"
