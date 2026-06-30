"""
Tests for the SQLite Candidate Database
"""

import os
import tempfile
import unittest
from pathlib import Path

from openevolve.candidate_db import CandidateDB, CandidateStatus


class TestCandidateDB(unittest.TestCase):
    """Test cases for CandidateDB"""
    
    def setUp(self):
        """Set up test database"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_candidates.db")
        self.db = CandidateDB(self.db_path)
    
    def tearDown(self):
        """Clean up test database"""
        self.db.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)
    
    def test_init_creates_database(self):
        """Test that database file is created"""
        self.assertTrue(os.path.exists(self.db_path))
    
    def test_add_candidate_basic(self):
        """Test adding a basic candidate"""
        candidate_id = self.db.add_candidate(
            patch_diff="+ improved code",
            raw_output="Test passed",
            score=0.95,
            status=CandidateStatus.SUCCESS
        )
        
        self.assertIsNotNone(candidate_id)
        self.assertTrue(len(candidate_id) > 0)
    
    def test_add_candidate_with_parent(self):
        """Test adding a candidate with a parent"""
        # Add parent
        parent_id = self.db.add_candidate(
            patch_diff="+ initial code",
            score=0.80,
            status=CandidateStatus.SUCCESS
        )
        
        # Add child
        child_id = self.db.add_candidate(
            parent_id=parent_id,
            patch_diff="+ improved code",
            score=0.90,
            status=CandidateStatus.SUCCESS
        )
        
        # Verify child has correct parent
        child = self.db.get_candidate(child_id)
        self.assertEqual(child['parent_id'], parent_id)
    
    def test_add_candidate_with_metadata(self):
        """Test adding candidate with metadata"""
        metadata = {
            "iteration": 42,
            "island": 0,
            "complexity": 100,
            "diversity": 0.5
        }
        
        candidate_id = self.db.add_candidate(
            score=0.85,
            status=CandidateStatus.SUCCESS,
            metadata=metadata
        )
        
        # Retrieve and verify
        candidate = self.db.get_candidate(candidate_id)
        self.assertEqual(candidate['metadata']['iteration'], 42)
        self.assertEqual(candidate['metadata']['island'], 0)
    
    def test_add_candidate_with_metrics(self):
        """Test adding candidate with multiple metrics"""
        metrics = {
            "latency": 15.5,
            "latency_score": 0.95,
            "throughput": 2500.0,
            "throughput_score": 0.88
        }
        
        candidate_id = self.db.add_candidate(
            score=0.91,
            status=CandidateStatus.SUCCESS,
            metrics=metrics
        )
        
        # Retrieve and verify
        candidate = self.db.get_candidate(candidate_id)
        self.assertIn('metrics', candidate)
        self.assertEqual(candidate['metrics']['latency'], 15.5)
        self.assertEqual(candidate['metrics']['throughput'], 2500.0)
    
    def test_get_candidate(self):
        """Test retrieving a candidate by ID"""
        candidate_id = self.db.add_candidate(
            patch_diff="+ test code",
            raw_output="stdout content",
            score=0.88,
            status=CandidateStatus.SUCCESS
        )
        
        candidate = self.db.get_candidate(candidate_id)
        
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['candidate_id'], candidate_id)
        self.assertEqual(candidate['score'], 0.88)
        self.assertEqual(candidate['status'], CandidateStatus.SUCCESS.value)
    
    def test_get_candidate_not_found(self):
        """Test getting non-existent candidate"""
        candidate = self.db.get_candidate("nonexistent-uuid")
        self.assertIsNone(candidate)
    
    def test_update_candidate(self):
        """Test updating candidate information"""
        # Add candidate
        candidate_id = self.db.add_candidate(
            score=0.70,
            status=CandidateStatus.PENDING
        )
        
        # Update it
        success = self.db.update_candidate(
            candidate_id,
            raw_output="Test completed",
            score=0.95,
            status=CandidateStatus.SUCCESS
        )
        
        self.assertTrue(success)
        
        # Verify update
        candidate = self.db.get_candidate(candidate_id)
        self.assertEqual(candidate['score'], 0.95)
        self.assertEqual(candidate['status'], CandidateStatus.SUCCESS.value)
        self.assertEqual(candidate['raw_output'], "Test completed")
    
    def test_update_candidate_metadata(self):
        """Test updating and merging metadata"""
        # Add with initial metadata
        candidate_id = self.db.add_candidate(
            score=0.80,
            status=CandidateStatus.SUCCESS,
            metadata={"iteration": 1, "island": 0}
        )
        
        # Update with additional metadata
        self.db.update_candidate(
            candidate_id,
            metadata={"complexity": 100, "diversity": 0.5}
        )
        
        # Verify metadata merged
        candidate = self.db.get_candidate(candidate_id)
        self.assertEqual(candidate['metadata']['iteration'], 1)
        self.assertEqual(candidate['metadata']['complexity'], 100)
    
    def test_get_top_candidates(self):
        """Test retrieving top candidates by score"""
        # Add multiple candidates
        scores = [0.50, 0.95, 0.70, 0.85, 0.60]
        for score in scores:
            self.db.add_candidate(
                score=score,
                status=CandidateStatus.SUCCESS
            )
        
        # Get top 3
        top = self.db.get_top_candidates(limit=3)
        
        self.assertEqual(len(top), 3)
        self.assertEqual(top[0]['score'], 0.95)
        self.assertEqual(top[1]['score'], 0.85)
        self.assertEqual(top[2]['score'], 0.70)
    
    def test_get_candidates_by_status(self):
        """Test filtering candidates by status"""
        # Add candidates with different statuses
        self.db.add_candidate(score=0.80, status=CandidateStatus.SUCCESS)
        self.db.add_candidate(score=0.70, status=CandidateStatus.SUCCESS)
        self.db.add_candidate(score=0.40, status=CandidateStatus.REGRESSION)
        self.db.add_candidate(score=0.00, status=CandidateStatus.BUILD_ERROR)
        self.db.add_candidate(score=0.00, status=CandidateStatus.TIMEOUT)
        
        # Get successful candidates
        successes = self.db.get_candidates_by_status(CandidateStatus.SUCCESS)
        self.assertEqual(len(successes), 2)
        
        # Get regressions
        regressions = self.db.get_candidates_by_status(CandidateStatus.REGRESSION)
        self.assertEqual(len(regressions), 1)
        
        # Get build errors
        errors = self.db.get_candidates_by_status(CandidateStatus.BUILD_ERROR)
        self.assertEqual(len(errors), 1)
    
    def test_lineage_tracking(self):
        """Test lineage tracking across generations"""
        # Create a lineage: gen0 -> gen1 -> gen2 -> gen3
        gen0 = self.db.add_candidate(score=0.50, status=CandidateStatus.SUCCESS)
        gen1 = self.db.add_candidate(
            parent_id=gen0,
            score=0.60,
            status=CandidateStatus.SUCCESS
        )
        gen2 = self.db.add_candidate(
            parent_id=gen1,
            score=0.70,
            status=CandidateStatus.SUCCESS
        )
        gen3 = self.db.add_candidate(
            parent_id=gen2,
            score=0.80,
            status=CandidateStatus.SUCCESS
        )
        
        # Get lineage of gen3
        lineage = self.db.get_lineage(gen3)
        
        self.assertEqual(len(lineage), 3)
        
        # Should be ordered by generation distance
        self.assertEqual(lineage[0][0], gen2)
        self.assertEqual(lineage[0][1], 1)  # Distance 1
        
        self.assertEqual(lineage[1][0], gen1)
        self.assertEqual(lineage[1][1], 2)  # Distance 2
        
        self.assertEqual(lineage[2][0], gen0)
        self.assertEqual(lineage[2][1], 3)  # Distance 3
    
    def test_lineage_max_generations(self):
        """Test limiting lineage depth"""
        # Create lineage
        gen0 = self.db.add_candidate(score=0.50, status=CandidateStatus.SUCCESS)
        gen1 = self.db.add_candidate(parent_id=gen0, score=0.60, status=CandidateStatus.SUCCESS)
        gen2 = self.db.add_candidate(parent_id=gen1, score=0.70, status=CandidateStatus.SUCCESS)
        gen3 = self.db.add_candidate(parent_id=gen2, score=0.80, status=CandidateStatus.SUCCESS)
        
        # Get only 2 generations back
        lineage = self.db.get_lineage(gen3, max_generations=2)
        
        self.assertEqual(len(lineage), 2)
        self.assertEqual(lineage[0][0], gen2)
        self.assertEqual(lineage[1][0], gen1)
    
    def test_get_stats(self):
        """Test getting database statistics"""
        # Add various candidates
        self.db.add_candidate(score=0.95, status=CandidateStatus.SUCCESS)
        self.db.add_candidate(score=0.85, status=CandidateStatus.SUCCESS)
        self.db.add_candidate(score=0.40, status=CandidateStatus.REGRESSION)
        self.db.add_candidate(score=0.00, status=CandidateStatus.BUILD_ERROR)
        
        stats = self.db.get_stats()
        
        self.assertEqual(stats['total_candidates'], 4)
        self.assertEqual(stats['best_score'], 0.95)
        self.assertAlmostEqual(stats['avg_score'], 0.55, places=2)
        self.assertEqual(stats['status_counts'][CandidateStatus.SUCCESS.value], 2)
        self.assertEqual(stats['status_counts'][CandidateStatus.REGRESSION.value], 1)
    
    def test_context_manager(self):
        """Test using database as context manager"""
        db_path = os.path.join(self.temp_dir, "context_test.db")
        
        with CandidateDB(db_path) as db:
            candidate_id = db.add_candidate(
                score=0.88,
                status=CandidateStatus.SUCCESS
            )
            self.assertIsNotNone(candidate_id)
        
        # Should be closed now
        self.assertTrue(os.path.exists(db_path))
        
        # Clean up
        os.remove(db_path)
    
    def test_status_enum_values(self):
        """Test all status enum values"""
        statuses = [
            CandidateStatus.SUCCESS,
            CandidateStatus.REGRESSION,
            CandidateStatus.BUILD_ERROR,
            CandidateStatus.TIMEOUT,
            CandidateStatus.PARSE_ERROR,
            CandidateStatus.RUNTIME_ERROR,
            CandidateStatus.PENDING
        ]
        
        for status in statuses:
            candidate_id = self.db.add_candidate(
                score=0.5,
                status=status
            )
            
            candidate = self.db.get_candidate(candidate_id)
            self.assertEqual(candidate['status'], status.value)
    
    def test_persistence_across_connections(self):
        """Test that data persists across database connections"""
        # Add candidate and close
        candidate_id = self.db.add_candidate(
            patch_diff="+ test code",
            score=0.88,
            status=CandidateStatus.SUCCESS
        )
        self.db.close()
        
        # Reopen database
        db2 = CandidateDB(self.db_path)
        
        # Verify data still exists
        candidate = db2.get_candidate(candidate_id)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['score'], 0.88)
        
        db2.close()
    
    def test_large_raw_output(self):
        """Test storing large raw output"""
        large_output = "X" * 100000  # 100KB of output
        
        candidate_id = self.db.add_candidate(
            raw_output=large_output,
            score=0.75,
            status=CandidateStatus.SUCCESS
        )
        
        candidate = self.db.get_candidate(candidate_id)
        self.assertEqual(len(candidate['raw_output']), 100000)
    
    def test_empty_values(self):
        """Test handling of None/empty values"""
        candidate_id = self.db.add_candidate(
            parent_id=None,
            patch_diff=None,
            raw_output=None,
            score=None,
            status=CandidateStatus.PENDING
        )
        
        candidate = self.db.get_candidate(candidate_id)
        self.assertIsNone(candidate['parent_id'])
        self.assertIsNone(candidate['patch_diff'])
        self.assertIsNone(candidate['score'])


class TestCandidateDBIntegration(unittest.TestCase):
    """Integration tests for CandidateDB with realistic scenarios"""
    
    def setUp(self):
        """Set up test database"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "integration_test.db")
        self.db = CandidateDB(self.db_path)
    
    def tearDown(self):
        """Clean up"""
        self.db.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)
    
    def test_evolution_workflow(self):
        """Test a complete evolution workflow"""
        # Initial candidate
        gen0 = self.db.add_candidate(
            patch_diff="+ def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)",
            raw_output="Execution time: 2.5s\nTest passed",
            score=0.60,
            status=CandidateStatus.SUCCESS,
            metadata={"iteration": 0, "island": 0},
            metrics={"execution_time": 2.5, "execution_time_score": 0.60}
        )
        
        # Improved candidate
        gen1 = self.db.add_candidate(
            parent_id=gen0,
            patch_diff="+ @lru_cache\n+ def fib(n): ...",
            raw_output="Execution time: 0.5s\nTest passed",
            score=0.95,
            status=CandidateStatus.SUCCESS,
            metadata={"iteration": 1, "island": 0},
            metrics={"execution_time": 0.5, "execution_time_score": 0.95}
        )
        
        # Regression
        gen2_bad = self.db.add_candidate(
            parent_id=gen1,
            patch_diff="+ def fib(n): return 42  # wrong!",
            raw_output="Test failed: assertion error",
            score=0.0,
            status=CandidateStatus.REGRESSION,
            metadata={"iteration": 2, "island": 0}
        )
        
        # Build error
        gen2_error = self.db.add_candidate(
            parent_id=gen1,
            patch_diff="+ def fib(n: int -> int  # syntax error",
            raw_output="SyntaxError: invalid syntax",
            score=0.0,
            status=CandidateStatus.BUILD_ERROR,
            metadata={"iteration": 2, "island": 0}
        )
        
        # Further improvement
        gen3 = self.db.add_candidate(
            parent_id=gen1,
            patch_diff="+ using dynamic programming",
            raw_output="Execution time: 0.1s\nTest passed",
            score=0.98,
            status=CandidateStatus.SUCCESS,
            metadata={"iteration": 3, "island": 0},
            metrics={"execution_time": 0.1, "execution_time_score": 0.98}
        )
        
        # Verify top candidates
        top = self.db.get_top_candidates(limit=3)
        self.assertEqual(top[0]['candidate_id'], gen3)
        self.assertEqual(top[1]['candidate_id'], gen1)
        self.assertEqual(top[2]['candidate_id'], gen0)
        
        # Verify failures
        build_errors = self.db.get_candidates_by_status(CandidateStatus.BUILD_ERROR)
        self.assertEqual(len(build_errors), 1)
        
        regressions = self.db.get_candidates_by_status(CandidateStatus.REGRESSION)
        self.assertEqual(len(regressions), 1)
        
        # Verify lineage
        lineage = self.db.get_lineage(gen3)
        self.assertEqual(len(lineage), 2)  # gen1 and gen0
    
    def test_crash_recovery_scenario(self):
        """Test that we can recover from a crash mid-evolution"""
        # Simulate 10 iterations
        parent_id = None
        for i in range(10):
            parent_id = self.db.add_candidate(
                parent_id=parent_id,
                score=0.5 + (i * 0.05),
                status=CandidateStatus.SUCCESS,
                metadata={"iteration": i}
            )
        
        # Simulate crash by closing connection
        self.db.close()
        
        # Reopen and verify all data is there
        db2 = CandidateDB(self.db_path)
        stats = db2.get_stats()
        
        self.assertEqual(stats['total_candidates'], 10)
        self.assertAlmostEqual(stats['best_score'], 0.95, places=2)
        
        db2.close()


if __name__ == '__main__':
    unittest.main()
