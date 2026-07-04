"""
Tests for OptimizerLoop audit-table extensions on ProgramDatabase.
"""

import random
import sqlite3
import unittest

from openevolve.config import Config
from openevolve.database import CandidateDatabase, Program, ProgramDatabase


class TestOptimizerLoopDatabaseExtensions(unittest.TestCase):
    """Tests for runs/candidates/audit_log companion storage."""

    def setUp(self):
        config = Config()
        config.database.in_memory = True
        self.db = ProgramDatabase(config.database)

    def tearDown(self):
        self.db.close()

    def test_program_add_mirrors_candidate_for_export(self):
        program = Program(
            id="baseline",
            code="def baseline(): return 1",
            metrics={"score": 1.0},
            generation=0,
        )

        self.db.add(program)

        export = self.db.export_run()
        self.assertEqual(export["run"]["id"], self.db.current_run_id)
        self.assertEqual(len(export["candidates"]), 1)
        self.assertEqual(export["candidates"][0]["id"], "baseline")
        self.assertEqual(export["candidates"][0]["metrics"]["score"], 1.0)
        self.assertEqual(export["best_candidate"]["id"], "baseline")
        self.assertGreaterEqual(len(export["audit_log"]), 2)

    def test_property_database_referential_integrity_random_insertions(self):
        """Property 7: parent_id values reference existing candidates or are NULL."""
        run_id = self.db.create_run(run_id="referential-run")
        rng = random.Random(42)
        candidate_ids = []

        for generation in range(75):
            parent_id = rng.choice(candidate_ids + [None]) if candidate_ids else None
            candidate_id = f"candidate-{generation}"
            self.db.insert_candidate(
                candidate_id=candidate_id,
                run_id=run_id,
                generation=generation,
                parent_id=parent_id,
                patch_content=f"+ change {generation}",
                metrics={"score": float(generation)},
                score=float(generation),
            )
            candidate_ids.append(candidate_id)

        for candidate_id in candidate_ids:
            candidate = self.db.get_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            if candidate["parent_id"] is not None:
                self.assertIsNotNone(self.db.get_candidate(candidate["parent_id"]))

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_candidate(
                candidate_id="orphan",
                run_id=run_id,
                generation=999,
                parent_id="missing-parent",
                patch_content="+ orphan",
            )

    def test_export_run_and_recent_failures(self):
        run_id = self.db.create_run(run_id="export-run", target_repo="repo")
        self.db.insert_candidate(
            candidate_id="baseline",
            run_id=run_id,
            generation=0,
            patch_content="",
            metrics={"score": 1.0},
            score=1.0,
        )
        self.db.record_failure(
            candidate_id="failed-apply",
            run_id=run_id,
            generation=1,
            parent_id="baseline",
            failure_phase="apply",
            error_message="patch did not apply",
            patch_content="+ bad patch",
        )
        self.db.record_failure(
            candidate_id="failed-test",
            run_id=run_id,
            generation=2,
            parent_id="baseline",
            failure_phase="test",
            error_message="pytest failed",
            patch_content="+ slower code",
        )

        failures = self.db.get_recent_failures(window=1, run_id=run_id)
        self.assertEqual(len(failures), 1)
        self.assertIn("failed during test", failures[0])
        self.assertIn("pytest failed", failures[0])

        export = self.db.export_run(run_id)
        self.assertEqual(export["run"]["id"], run_id)
        self.assertEqual(len(export["candidates"]), 3)
        self.assertEqual(len(export["failures"]), 2)
        self.assertTrue(
            any(event["event_type"] == "candidate_recorded" for event in export["audit_log"])
        )

    def test_candidate_database_accepts_sqlite_path(self):
        with CandidateDatabase(":memory:") as candidate_db:
            run_id = candidate_db.create_run(run_id="candidate-db-run")
            candidate_id = candidate_db.insert_candidate(
                run_id=run_id,
                generation=0,
                patch_content="+ direct candidate",
                score=0.5,
            )
            self.assertIsNotNone(candidate_db.get_candidate(candidate_id))


if __name__ == "__main__":
    unittest.main()
