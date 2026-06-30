#!/usr/bin/env python3
"""
Demonstration of SQLite Candidate Database

This example shows how the candidate database provides crash-resistant
persistence and detailed tracking for evolutionary optimization.
"""

import tempfile
import os
from openevolve.candidate_db import CandidateDB, CandidateStatus


def demo_basic_usage():
    """Demo 1: Basic candidate tracking"""
    print("\n" + "="*70)
    print("DEMO 1: Basic Candidate Tracking")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "demo.db")
        
        with CandidateDB(db_path) as db:
            # Add initial candidate
            gen0 = db.add_candidate(
                patch_diff="+ def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)",
                raw_output="Execution time: 2.5s\nTest passed",
                score=0.60,
                status=CandidateStatus.SUCCESS,
                metadata={"iteration": 0}
            )
            
            print(f"\n✓ Added initial candidate: {gen0[:8]}")
            print(f"  Score: 0.60")
            print(f"  Status: Success")
            
            # Add improved candidate
            gen1 = db.add_candidate(
                parent_id=gen0,
                patch_diff="+ @lru_cache()\n+ def fib(n): ...",
                raw_output="Execution time: 0.5s\nTest passed",
                score=0.95,
                status=CandidateStatus.SUCCESS,
                metadata={"iteration": 1}
            )
            
            print(f"\n✓ Added improved candidate: {gen1[:8]}")
            print(f"  Parent: {gen0[:8]}")
            print(f"  Score: 0.95 (+0.35 improvement)")
            
            # Get candidate details
            candidate = db.get_candidate(gen1)
            print(f"\n✓ Retrieved candidate details:")
            print(f"  ID: {candidate['candidate_id'][:8]}")
            print(f"  Score: {candidate['score']}")
            print(f"  Status: {candidate['status']}")
            print(f"  Iteration: {candidate['metadata']['iteration']}")


def demo_failure_tracking():
    """Demo 2: Tracking failures"""
    print("\n" + "="*70)
    print("DEMO 2: Failure Tracking")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "failures.db")
        
        with CandidateDB(db_path) as db:
            # Add successful parent
            parent = db.add_candidate(
                score=0.85,
                status=CandidateStatus.SUCCESS
            )
            
            # Add build error
            db.add_candidate(
                parent_id=parent,
                patch_diff="+ def fib(n: int -> int  # syntax error",
                raw_output="SyntaxError: invalid syntax at line 5",
                score=0.0,
                status=CandidateStatus.BUILD_ERROR,
                metadata={"iteration": 2, "error_type": "syntax"}
            )
            
            # Add regression
            db.add_candidate(
                parent_id=parent,
                patch_diff="+ slower algorithm",
                raw_output="Test passed but slower: 5.2s",
                score=0.40,
                status=CandidateStatus.REGRESSION,
                metadata={"iteration": 3}
            )
            
            # Add timeout
            db.add_candidate(
                parent_id=parent,
                raw_output="Evaluation timed out after 300s",
                score=0.0,
                status=CandidateStatus.TIMEOUT,
                metadata={"iteration": 4}
            )
            
            print("\n✓ Added 3 failures:")
            
            # Query failures
            errors = db.get_candidates_by_status(CandidateStatus.BUILD_ERROR)
            print(f"  - Build errors: {len(errors)}")
            
            regressions = db.get_candidates_by_status(CandidateStatus.REGRESSION)
            print(f"  - Regressions: {len(regressions)}")
            
            timeouts = db.get_candidates_by_status(CandidateStatus.TIMEOUT)
            print(f"  - Timeouts: {len(timeouts)}")


def demo_lineage_tracking():
    """Demo 3: Lineage and ancestry tracking"""
    print("\n" + "="*70)
    print("DEMO 3: Lineage Tracking")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "lineage.db")
        
        with CandidateDB(db_path) as db:
            # Create a multi-generation lineage
            print("\n✓ Creating lineage:")
            scores = [0.50, 0.60, 0.75, 0.85, 0.92]
            parent_id = None
            
            for i, score in enumerate(scores):
                candidate_id = db.add_candidate(
                    parent_id=parent_id,
                    score=score,
                    status=CandidateStatus.SUCCESS,
                    metadata={"generation": i}
                )
                print(f"  Gen {i}: {candidate_id[:8]} (score: {score})")
                parent_id = candidate_id
            
            # Query lineage
            lineage = db.get_lineage(parent_id)
            
            print(f"\n✓ Lineage of final candidate ({parent_id[:8]}):")
            for ancestor_id, distance in lineage:
                ancestor = db.get_candidate(ancestor_id)
                print(f"  -{distance} generation(s): {ancestor_id[:8]} "
                      f"(score: {ancestor['score']})")


def demo_multi_metric():
    """Demo 4: Multiple metrics per candidate"""
    print("\n" + "="*70)
    print("DEMO 4: Multi-Metric Tracking")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "metrics.db")
        
        with CandidateDB(db_path) as db:
            # Add candidate with multiple metrics
            candidate_id = db.add_candidate(
                score=0.91,
                status=CandidateStatus.SUCCESS,
                metadata={"iteration": 10},
                metrics={
                    "latency": 15.5,
                    "latency_score": 0.95,
                    "throughput": 2500,
                    "throughput_score": 0.88,
                    "memory": 128,
                    "memory_score": 0.90
                }
            )
            
            print(f"\n✓ Added candidate with multiple metrics: {candidate_id[:8]}")
            
            # Retrieve and display metrics
            candidate = db.get_candidate(candidate_id)
            print(f"\n✓ Metrics:")
            print(f"  Latency: {candidate['metrics']['latency']}ms "
                  f"(score: {candidate['metrics']['latency_score']})")
            print(f"  Throughput: {candidate['metrics']['throughput']} ops/sec "
                  f"(score: {candidate['metrics']['throughput_score']})")
            print(f"  Memory: {candidate['metrics']['memory']}MB "
                  f"(score: {candidate['metrics']['memory_score']})")
            print(f"  Combined score: {candidate['score']}")


def demo_statistics():
    """Demo 5: Database statistics"""
    print("\n" + "="*70)
    print("DEMO 5: Database Statistics")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "stats.db")
        
        with CandidateDB(db_path) as db:
            # Add various candidates
            scores = [0.50, 0.60, 0.95, 0.75, 0.85, 0.30, 0.70]
            statuses = [
                CandidateStatus.SUCCESS,
                CandidateStatus.SUCCESS,
                CandidateStatus.SUCCESS,
                CandidateStatus.SUCCESS,
                CandidateStatus.SUCCESS,
                CandidateStatus.REGRESSION,
                CandidateStatus.BUILD_ERROR
            ]
            
            for score, status in zip(scores, statuses):
                db.add_candidate(score=score, status=status)
            
            # Get statistics
            stats = db.get_stats()
            
            print(f"\n✓ Database Statistics:")
            print(f"  Total candidates: {stats['total_candidates']}")
            print(f"  Best score: {stats['best_score']}")
            print(f"  Average score: {stats['avg_score']:.3f}")
            print(f"  Recent (24h): {stats['recent_candidates']}")
            
            print(f"\n✓ Status Breakdown:")
            for status, count in stats['status_counts'].items():
                print(f"  {status}: {count}")


def demo_crash_recovery():
    """Demo 6: Crash recovery simulation"""
    print("\n" + "="*70)
    print("DEMO 6: Crash Recovery")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "recovery.db")
        
        # First session - add some candidates
        print("\n✓ Session 1: Adding candidates...")
        with CandidateDB(db_path) as db:
            for i in range(5):
                db.add_candidate(
                    score=0.5 + (i * 0.1),
                    status=CandidateStatus.SUCCESS,
                    metadata={"iteration": i}
                )
            print(f"  Added 5 candidates")
        
        # Simulate crash
        print("\n⚠  [SIMULATED CRASH]")
        
        # Second session - recover
        print("\n✓ Session 2: Recovering from crash...")
        with CandidateDB(db_path) as db:
            stats = db.get_stats()
            print(f"  Recovered {stats['total_candidates']} candidates")
            
            # Get best candidate to resume
            top = db.get_top_candidates(limit=1)
            if top:
                best = top[0]
                last_iter = best['metadata'].get('iteration', 0)
                print(f"  Best candidate: score={best['score']:.2f}")
                print(f"  Resuming from iteration {last_iter}")
                
                # Continue evolution
                db.add_candidate(
                    parent_id=best['candidate_id'],
                    score=0.95,
                    status=CandidateStatus.SUCCESS,
                    metadata={"iteration": last_iter + 1}
                )
                print(f"  ✓ Added new candidate, continuing evolution")


def demo_top_performers():
    """Demo 7: Querying top performers"""
    print("\n" + "="*70)
    print("DEMO 7: Top Performers")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "top.db")
        
        with CandidateDB(db_path) as db:
            # Add many candidates
            import random
            for i in range(20):
                score = random.uniform(0.3, 0.99)
                db.add_candidate(
                    score=score,
                    status=CandidateStatus.SUCCESS,
                    metadata={"iteration": i}
                )
            
            # Get top 5
            top = db.get_top_candidates(limit=5)
            
            print(f"\n✓ Top 5 Candidates:")
            for i, candidate in enumerate(top, 1):
                print(f"  {i}. {candidate['candidate_id'][:8]}: "
                      f"score={candidate['score']:.3f} "
                      f"(iter {candidate['metadata']['iteration']})")


def main():
    """Run all demonstrations"""
    print("\n" + "="*70)
    print("SQLITE CANDIDATE DATABASE DEMO")
    print("="*70)
    print("\nThis demo shows how the candidate database provides crash-resistant")
    print("persistence and detailed tracking for evolutionary optimization.")
    
    demo_basic_usage()
    demo_failure_tracking()
    demo_lineage_tracking()
    demo_multi_metric()
    demo_statistics()
    demo_crash_recovery()
    demo_top_performers()
    
    print("\n" + "="*70)
    print("All demos completed successfully!")
    print("="*70)
    print("\nNext steps:")
    print("1. Integrate candidate DB into your evolution loop")
    print("2. Query database for analytics and dashboards")
    print("3. See docs/candidate_db_guide.md for detailed documentation")
    print()


if __name__ == "__main__":
    main()
