"""Property tests for optimizer-loop workspace isolation requirements."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st

from openevolve.workspace_manager import WorkspaceManager


SAFE_LINE = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="_-",
        max_codepoint=127,
    ),
    min_size=1,
    max_size=40,
)


def _create_repository(root: Path, content: str = "baseline") -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "OptimizerLoop Tests"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "optimizer@example.test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "value.txt").write_text(f"{content}\n", encoding="utf-8")
    subprocess.run(["git", "add", "value.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def _single_line_patch(old: str, new: str) -> str:
    return (
        "--- a/value.txt\n"
        "+++ b/value.txt\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


@given(original=SAFE_LINE, replacement=SAFE_LINE, conflict=st.booleans())
@settings(max_examples=20, deadline=None)
def test_property_patch_validation_status_assignment(
    original: str,
    replacement: str,
    conflict: bool,
) -> None:
    """Property 4: status is passed exactly when a patch applies cleanly."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        repo = _create_repository(root, original)
        manager = WorkspaceManager(
            str(repo),
            worktree_parent_dir=str(root / "worktrees"),
            min_disk_space_mb=0,
        )
        worktree = Path(manager.create_worktree())
        old_line = f"conflicting-{original}" if conflict else original

        result = manager.apply_patch(
            worktree,
            _single_line_patch(old_line, replacement),
        )

        assert result.status == ("failed" if conflict else "passed")
        assert result.success is (not conflict)
        if conflict:
            assert result.error_output
            assert not worktree.exists()
        else:
            assert (worktree / "value.txt").read_text(encoding="utf-8") == (
                f"{replacement}\n"
            )
            assert manager.verify_clean_application(worktree)
            manager.cleanup_worktree(worktree)
            assert not worktree.exists()


@given(outcome=st.sampled_from(("success", "failure", "interrupt")))
@settings(max_examples=9, deadline=None)
def test_property_worktree_cleanup_invariant(outcome: str) -> None:
    """Property 8: every completed execution path removes its worktree."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        repo = _create_repository(root)
        manager = WorkspaceManager(
            str(repo),
            worktree_parent_dir=str(root / "worktrees"),
            min_disk_space_mb=0,
        )
        worktree = None

        try:
            with manager as worktree_path:
                worktree = Path(worktree_path)
                assert worktree.exists()
                if outcome == "failure":
                    raise RuntimeError("simulated test failure")
                if outcome == "interrupt":
                    raise KeyboardInterrupt("simulated interrupt")
        except (RuntimeError, KeyboardInterrupt):
            pass

        assert worktree is not None
        assert not worktree.exists()
        registered = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert str(worktree) not in registered.stdout
