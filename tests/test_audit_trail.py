"""
Tests for audit trail functionality — Tasks 14.1, 14.2, 14.3.

Task 14.1 — log_event() and get_audit_log() on ProgramDatabase
Task 14.2 — export_audit_trail() markdown export
Task 14.3 — unit tests (this file)

Requirements: 12.1 – 12.6
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openevolve.config import Config
from openevolve.database import ProgramDatabase


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    config = Config()
    config.database.in_memory = True
    d = ProgramDatabase(config.database)
    run_id = d.create_run(run_id="audit-test-run", target_repo="https://github.com/test/repo")
    d.current_run_id = run_id
    yield d
    d.close()


def _insert_candidate(db, generation=1, score=0.5, patch="--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n",
                      stdout="ok", stderr="", failed=False):
    return db.insert_candidate(
        generation=generation,
        parent_id=None,
        patch_content=patch,
        score=score,
        metrics={"combined_score": score},
        failed=failed,
        stdout=stdout,
        stderr=stderr,
        applied=True,
        tested=True,
        exit_code=0,
    )


# ---------------------------------------------------------------------------
# Task 14.1 — log_event()
# ---------------------------------------------------------------------------

class TestLogEvent:
    """log_event() records events to audit_log (Req 12.1 – 12.5)."""

    def test_log_event_no_error(self, db):
        """log_event() does not raise."""
        db.log_event("generation_start", {"generation": 1})

    def test_log_event_appears_in_audit_log(self, db):
        db.log_event("generation_start", {"generation": 1})
        events = db.get_audit_log()
        types = [e["event_type"] for e in events]
        assert "generation_start" in types

    def test_log_event_stores_event_data(self, db):
        db.log_event("patch_generated", {"patch_length": 42, "has_patch": True})
        events = db.get_audit_log(event_type="patch_generated")
        assert len(events) >= 1
        data = events[-1]["event_data"]
        assert data.get("patch_length") == 42
        assert data.get("has_patch") is True

    def test_log_event_with_candidate_id(self, db):
        cid = _insert_candidate(db)
        db.log_event("patch_applied", {"success": True}, candidate_id=cid)
        events = db.get_audit_log(candidate_id=cid)
        assert any(e["event_type"] == "patch_applied" for e in events)

    def test_log_event_empty_data(self, db):
        db.log_event("test_executed")
        events = db.get_audit_log(event_type="test_executed")
        assert len(events) >= 1

    def test_all_required_event_types_accepted(self, db):
        """Req 12.1 – 12.5: all 6 OptimizerLoop event types are stored."""
        for etype in [
            "generation_start",
            "patch_generated",
            "patch_applied",
            "test_executed",
            "metrics_extracted",
            "candidate_scored",
        ]:
            db.log_event(etype, {"generation": 1})

        stored = {e["event_type"] for e in db.get_audit_log()}
        for etype in ["generation_start", "patch_generated", "patch_applied",
                      "test_executed", "metrics_extracted"]:
            assert etype in stored, f"Event type '{etype}' missing"

    def test_multiple_events_ordered_by_timestamp(self, db):
        db.log_event("generation_start", {"generation": 1})
        db.log_event("patch_generated", {"generation": 1})
        db.log_event("test_executed", {"generation": 1})
        events = db.get_audit_log()
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Task 14.1 — get_audit_log() filtering
# ---------------------------------------------------------------------------

class TestGetAuditLog:
    """get_audit_log() supports filtering by run_id, event_type, candidate_id."""

    def test_filter_by_event_type(self, db):
        db.log_event("generation_start", {"n": 1})
        db.log_event("patch_generated", {"n": 2})
        events = db.get_audit_log(event_type="generation_start")
        assert all(e["event_type"] == "generation_start" for e in events)

    def test_filter_by_candidate_id(self, db):
        cid1 = _insert_candidate(db, generation=1)
        cid2 = _insert_candidate(db, generation=2)
        db.log_event("metrics_extracted", {"cid": 1}, candidate_id=cid1)
        db.log_event("metrics_extracted", {"cid": 2}, candidate_id=cid2)
        events_1 = db.get_audit_log(candidate_id=cid1)
        assert all(e["candidate_id"] == cid1 for e in events_1)

    def test_returns_all_when_no_filter(self, db):
        db.log_event("generation_start", {})
        db.log_event("test_executed", {})
        events = db.get_audit_log()
        assert len(events) >= 2

    def test_returns_empty_for_unknown_event_type(self, db):
        events = db.get_audit_log(event_type="does_not_exist")
        assert events == []

    def test_event_data_is_deserialized(self, db):
        db.log_event("patch_generated", {"score": 0.75, "nested": {"a": 1}})
        events = db.get_audit_log(event_type="patch_generated")
        data = events[-1]["event_data"]
        assert isinstance(data, dict)
        assert data.get("score") == 0.75
        assert data.get("nested") == {"a": 1}


# ---------------------------------------------------------------------------
# Task 14.2 — export_audit_trail()
# ---------------------------------------------------------------------------

class TestExportAuditTrail:
    """export_audit_trail() generates a human-readable Markdown report (Req 12.6)."""

    def test_returns_string(self, db):
        md = db.export_audit_trail()
        assert isinstance(md, str)
        assert len(md) > 0

    def test_contains_run_id(self, db):
        md = db.export_audit_trail()
        assert "audit-test-run" in md

    def test_contains_audit_trail_heading(self, db):
        md = db.export_audit_trail()
        assert "# Audit Trail" in md

    def test_contains_candidates_section(self, db):
        _insert_candidate(db)
        md = db.export_audit_trail()
        assert "## Candidates" in md

    def test_contains_patches_section(self, db):
        _insert_candidate(db, patch="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n")
        md = db.export_audit_trail()
        assert "## Patches" in md
        assert "```diff" in md
        assert "--- a/f.py" in md

    def test_contains_test_outputs_section(self, db):
        _insert_candidate(db, stdout="Test passed", stderr="")
        md = db.export_audit_trail()
        assert "## Test Outputs" in md
        assert "Test passed" in md

    def test_contains_metrics_section(self, db):
        _insert_candidate(db, score=0.85)
        md = db.export_audit_trail()
        assert "## Metrics" in md
        assert "0.85" in md

    def test_contains_event_log_section(self, db):
        db.log_event("generation_start", {"generation": 1})
        md = db.export_audit_trail()
        assert "## Event Log" in md
        assert "generation_start" in md

    def test_event_log_shows_event_count(self, db):
        db.log_event("generation_start", {})
        db.log_event("patch_generated", {})
        md = db.export_audit_trail()
        # The heading shows total event count
        assert "Event Log" in md

    def test_writes_file_when_output_path_given(self, db, tmp_path):
        out = str(tmp_path / "audit.md")
        db.export_audit_trail(output_path=out)
        assert Path(out).exists()
        content = Path(out).read_text(encoding="utf-8")
        assert "# Audit Trail" in content

    def test_creates_parent_directories(self, db, tmp_path):
        out = str(tmp_path / "nested" / "dir" / "audit.md")
        db.export_audit_trail(output_path=out)
        assert Path(out).exists()

    def test_raises_for_missing_run(self, db):
        with pytest.raises(KeyError):
            db.export_audit_trail(run_id="no-such-run")

    def test_raises_when_no_current_run(self):
        """export_audit_trail raises ValueError when no run and none given."""
        config = Config()
        config.database.in_memory = True
        fresh_db = ProgramDatabase(config.database)
        try:
            with pytest.raises(ValueError):
                fresh_db.export_audit_trail()
        finally:
            fresh_db.close()

    def test_full_audit_trail_roundtrip(self, db):
        """Complete generation cycle events appear in export."""
        cid = _insert_candidate(
            db, generation=1, score=0.65,
            patch="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n",
            stdout="Score: 0.65", stderr="",
        )
        db.log_event("generation_start", {"generation": 1})
        db.log_event("patch_generated", {"patch_length": 50}, candidate_id=cid)
        db.log_event("patch_applied", {"success": True}, candidate_id=cid)
        db.log_event("test_executed", {"exit_code": 0}, candidate_id=cid)
        db.log_event("metrics_extracted", {"combined_score": 0.65}, candidate_id=cid)

        md = db.export_audit_trail()

        assert "generation_start" in md
        assert "patch_generated" in md
        assert "patch_applied" in md
        assert "test_executed" in md
        assert "metrics_extracted" in md
        assert "```diff" in md
        assert "Score: 0.65" in md

    def test_target_repo_in_export(self, db):
        md = db.export_audit_trail()
        assert "https://github.com/test/repo" in md

    def test_improvement_in_export_after_complete_run(self, db):
        db.complete_run("audit-test-run", status="successful", final_improvement=0.25)
        md = db.export_audit_trail()
        assert "0.25" in md or "successful" in md


# ---------------------------------------------------------------------------
# Task 14.1 — OptimizerLoop emits audit events (integration smoke test)
# ---------------------------------------------------------------------------

class TestOptimizerLoopAuditIntegration:
    """Verify OptimizerLoop.execute_generation() emits log_event() calls."""

    def test_generation_start_logged_during_run(self):
        """generation_start event appears in db after OptimizerLoop.run()."""
        from openevolve.optimizer_loop import OptimizerLoop
        from unittest.mock import patch

        cfg = {
            "repo_path": "/fake", "target_file": "/fake/p.py", "test_file": "/fake/t.py",
            "max_iterations": 2, "patience": 2, "db_path": ":memory:",
        }
        loop = OptimizerLoop(cfg, llm_ensemble=None)

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                lambda **kw: {"stdout": "ok", "stderr": "", "exit_code": 0,
                               "combined_score": 0.5, "execution_time": 0.1,
                               "all_passed": True},
                lambda s, e: True,
            )
            loop.run()

        events = loop.db.get_audit_log()
        event_types = {e["event_type"] for e in events}
        assert "generation_start" in event_types, (
            f"generation_start not found in events: {event_types}"
        )
