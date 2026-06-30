"""
SQLite-based Candidate Database for LoopBench Evolution

This module provides persistent storage for all evolution candidates using SQLite,
ensuring that no progress is lost even if the system crashes. It tracks:
- candidate_id (UUID): Unique identifier
- parent_id (UUID): The candidate it was based on
- patch_diff (TEXT): Git diff output
- raw_output (TEXT): Complete stdout/stderr from sandbox
- score (FLOAT): Parsed performance metric
- status (TEXT): Success / Regression / Build Error / Timeout
- metadata (JSON): Additional information (metrics, iteration, timestamp, etc.)

This database serves as the foundation for dashboards and analytics.
"""

import logging
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class CandidateStatus(Enum):
    """Status values for evolution candidates"""
    SUCCESS = "Success"
    REGRESSION = "Regression"
    BUILD_ERROR = "Build Error"
    TIMEOUT = "Timeout"
    PARSE_ERROR = "Parse Error"
    RUNTIME_ERROR = "Runtime Error"
    PENDING = "Pending"


class CandidateDB:
    """
    SQLite-based database for tracking all evolution candidates.
    
    Provides crash-resistant persistence and detailed tracking of every
    candidate generated during evolution, serving as the foundation for
    dashboards and analytics.
    
    Example:
        db = CandidateDB("evolution_data/candidates.db")
        
        # Add a new candidate
        candidate_id = db.add_candidate(
            parent_id="parent-uuid",
            patch_diff="diff output...",
            raw_output="stdout/stderr...",
            score=0.95,
            status=CandidateStatus.SUCCESS,
            metadata={"iteration": 42, "metrics": {...}}
        )
        
        # Query candidates
        best_candidates = db.get_top_candidates(limit=10)
        recent_failures = db.get_candidates_by_status(CandidateStatus.BUILD_ERROR)
    """
    
    def __init__(self, db_path: str):
        """
        Initialize the candidate database.
        
        Args:
            db_path: Path to SQLite database file (will be created if doesn't exist)
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Connect to database with thread safety
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0
        )
        self.conn.row_factory = sqlite3.Row  # Enable column access by name
        
        # Initialize schema
        self._init_schema()
        
        logger.info(f"Initialized CandidateDB at {self.db_path}")
    
    def _init_schema(self):
        """Create database schema if it doesn't exist"""
        cursor = self.conn.cursor()
        
        # Main candidates table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id TEXT PRIMARY KEY,
                parent_id TEXT,
                patch_diff TEXT,
                raw_output TEXT,
                score REAL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,
                
                -- Indexes for common queries
                FOREIGN KEY (parent_id) REFERENCES candidates(candidate_id)
            )
        """)
        
        # Create indexes for fast queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_parent_id 
            ON candidates(parent_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status 
            ON candidates(status)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_score 
            ON candidates(score DESC)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at 
            ON candidates(created_at DESC)
        """)
        
        # Metrics tracking table (for multi-metric support)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidate_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                metric_score REAL,
                
                FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
                UNIQUE(candidate_id, metric_name)
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_metric_name 
            ON candidate_metrics(metric_name, metric_value DESC)
        """)
        
        # Evolution lineage tracking (for genealogy visualization)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lineage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL,
                ancestor_id TEXT NOT NULL,
                generation_distance INTEGER NOT NULL,
                
                FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
                FOREIGN KEY (ancestor_id) REFERENCES candidates(candidate_id),
                UNIQUE(candidate_id, ancestor_id)
            )
        """)
        
        self.conn.commit()
        logger.debug("Database schema initialized")
    
    def add_candidate(
        self,
        parent_id: Optional[str] = None,
        patch_diff: Optional[str] = None,
        raw_output: Optional[str] = None,
        score: Optional[float] = None,
        status: CandidateStatus = CandidateStatus.PENDING,
        metadata: Optional[Dict[str, Any]] = None,
        candidate_id: Optional[str] = None,
        metrics: Optional[Dict[str, float]] = None
    ) -> str:
        """
        Add a new candidate to the database.
        
        Args:
            parent_id: UUID of the parent candidate (None for initial)
            patch_diff: Git diff output showing changes
            raw_output: Complete stdout/stderr from evaluation
            score: Primary performance score (e.g., combined_score)
            status: Candidate status (Success, Regression, etc.)
            metadata: Additional data (iteration, timestamps, etc.)
            candidate_id: Optional UUID (auto-generated if not provided)
            metrics: Optional dictionary of metric_name -> value pairs
        
        Returns:
            candidate_id: UUID of the added candidate
        """
        if candidate_id is None:
            candidate_id = str(uuid.uuid4())
        
        # Serialize metadata to JSON
        metadata_json = json.dumps(metadata) if metadata else None
        
        cursor = self.conn.cursor()
        
        try:
            # Insert candidate
            cursor.execute("""
                INSERT INTO candidates 
                (candidate_id, parent_id, patch_diff, raw_output, score, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                candidate_id,
                parent_id,
                patch_diff,
                raw_output,
                score,
                status.value,
                metadata_json
            ))
            
            # Insert metrics if provided
            if metrics:
                for metric_name, metric_value in metrics.items():
                    if isinstance(metric_value, (int, float)):
                        # Check if there's a corresponding score metric
                        metric_score = metrics.get(f"{metric_name}_score")
                        
                        cursor.execute("""
                            INSERT OR REPLACE INTO candidate_metrics
                            (candidate_id, metric_name, metric_value, metric_score)
                            VALUES (?, ?, ?, ?)
                        """, (candidate_id, metric_name, metric_value, metric_score))
            
            # Update lineage tracking
            if parent_id:
                # Direct parent relationship (distance 1)
                cursor.execute("""
                    INSERT OR IGNORE INTO lineage
                    (candidate_id, ancestor_id, generation_distance)
                    VALUES (?, ?, 1)
                """, (candidate_id, parent_id))
                
                # Inherit ancestors from parent (distance + 1)
                cursor.execute("""
                    INSERT OR IGNORE INTO lineage
                    (candidate_id, ancestor_id, generation_distance)
                    SELECT ?, ancestor_id, generation_distance + 1
                    FROM lineage
                    WHERE candidate_id = ?
                """, (candidate_id, parent_id))
            
            self.conn.commit()
            
            logger.debug(
                f"Added candidate {candidate_id[:8]} with status {status.value}, "
                f"score {score}"
            )
            
            return candidate_id
            
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            logger.error(f"Failed to add candidate {candidate_id}: {e}")
            raise
    
    def update_candidate(
        self,
        candidate_id: str,
        raw_output: Optional[str] = None,
        score: Optional[float] = None,
        status: Optional[CandidateStatus] = None,
        metadata: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, float]] = None
    ) -> bool:
        """
        Update an existing candidate's information.
        
        Args:
            candidate_id: UUID of candidate to update
            raw_output: New raw output (if changed)
            score: New score
            status: New status
            metadata: Additional metadata (will be merged with existing)
            metrics: New metrics to add/update
        
        Returns:
            True if update succeeded, False if candidate not found
        """
        cursor = self.conn.cursor()
        
        # Check if candidate exists
        cursor.execute(
            "SELECT metadata FROM candidates WHERE candidate_id = ?",
            (candidate_id,)
        )
        row = cursor.fetchone()
        if not row:
            logger.warning(f"Candidate {candidate_id} not found for update")
            return False
        
        # Merge metadata if provided
        if metadata:
            existing_metadata = json.loads(row['metadata']) if row['metadata'] else {}
            existing_metadata.update(metadata)
            metadata = existing_metadata
        
        # Build update query dynamically
        updates = []
        params = []
        
        if raw_output is not None:
            updates.append("raw_output = ?")
            params.append(raw_output)
        
        if score is not None:
            updates.append("score = ?")
            params.append(score)
        
        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata))
        
        if updates:
            params.append(candidate_id)
            cursor.execute(
                f"UPDATE candidates SET {', '.join(updates)} WHERE candidate_id = ?",
                params
            )
        
        # Update metrics if provided
        if metrics:
            for metric_name, metric_value in metrics.items():
                if isinstance(metric_value, (int, float)):
                    metric_score = metrics.get(f"{metric_name}_score")
                    
                    cursor.execute("""
                        INSERT OR REPLACE INTO candidate_metrics
                        (candidate_id, metric_name, metric_value, metric_score)
                        VALUES (?, ?, ?, ?)
                    """, (candidate_id, metric_name, metric_value, metric_score))
        
        self.conn.commit()
        logger.debug(f"Updated candidate {candidate_id[:8]}")
        return True
    
    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a candidate by ID.
        
        Args:
            candidate_id: UUID of the candidate
        
        Returns:
            Dictionary with candidate data, or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT candidate_id, parent_id, patch_diff, raw_output, score, status,
                   created_at, metadata
            FROM candidates
            WHERE candidate_id = ?
        """, (candidate_id,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        # Convert row to dict
        candidate = dict(row)
        
        # Parse metadata JSON
        if candidate['metadata']:
            candidate['metadata'] = json.loads(candidate['metadata'])
        
        # Fetch associated metrics
        cursor.execute("""
            SELECT metric_name, metric_value, metric_score
            FROM candidate_metrics
            WHERE candidate_id = ?
        """, (candidate_id,))
        
        metrics = {}
        for metric_row in cursor.fetchall():
            metrics[metric_row['metric_name']] = metric_row['metric_value']
            if metric_row['metric_score'] is not None:
                metrics[f"{metric_row['metric_name']}_score"] = metric_row['metric_score']
        
        candidate['metrics'] = metrics
        
        return candidate
    
    def get_top_candidates(
        self,
        limit: int = 10,
        status: Optional[CandidateStatus] = None
    ) -> List[Dict[str, Any]]:
        """
        Get top candidates by score.
        
        Args:
            limit: Maximum number of candidates to return
            status: Filter by status (None for all)
        
        Returns:
            List of candidate dictionaries, sorted by score descending
        """
        cursor = self.conn.cursor()
        
        query = """
            SELECT candidate_id, parent_id, score, status, created_at, metadata
            FROM candidates
            WHERE score IS NOT NULL
        """
        params = []
        
        if status:
            query += " AND status = ?"
            params.append(status.value)
        
        query += " ORDER BY score DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        
        candidates = []
        for row in cursor.fetchall():
            candidate = dict(row)
            if candidate['metadata']:
                candidate['metadata'] = json.loads(candidate['metadata'])
            candidates.append(candidate)
        
        return candidates
    
    def get_candidates_by_status(
        self,
        status: CandidateStatus,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all candidates with a specific status.
        
        Args:
            status: Status to filter by
            limit: Maximum number to return (None for all)
        
        Returns:
            List of candidate dictionaries
        """
        cursor = self.conn.cursor()
        
        query = """
            SELECT candidate_id, parent_id, score, status, created_at, metadata
            FROM candidates
            WHERE status = ?
            ORDER BY created_at DESC
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        cursor.execute(query, (status.value,))
        
        candidates = []
        for row in cursor.fetchall():
            candidate = dict(row)
            if candidate['metadata']:
                candidate['metadata'] = json.loads(candidate['metadata'])
            candidates.append(candidate)
        
        return candidates
    
    def get_lineage(
        self,
        candidate_id: str,
        max_generations: Optional[int] = None
    ) -> List[Tuple[str, int]]:
        """
        Get the ancestry lineage of a candidate.
        
        Args:
            candidate_id: UUID of the candidate
            max_generations: Maximum generation distance (None for all)
        
        Returns:
            List of (ancestor_id, generation_distance) tuples
        """
        cursor = self.conn.cursor()
        
        query = """
            SELECT ancestor_id, generation_distance
            FROM lineage
            WHERE candidate_id = ?
        """
        params = [candidate_id]
        
        if max_generations:
            query += " AND generation_distance <= ?"
            params.append(max_generations)
        
        query += " ORDER BY generation_distance ASC"
        
        cursor.execute(query, params)
        
        return [(row['ancestor_id'], row['generation_distance']) for row in cursor.fetchall()]
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get database statistics.
        
        Returns:
            Dictionary with statistics:
            - total_candidates
            - status_counts
            - best_score
            - avg_score
            - recent_candidates (last 24 hours)
        """
        cursor = self.conn.cursor()
        
        # Total candidates
        cursor.execute("SELECT COUNT(*) as count FROM candidates")
        total = cursor.fetchone()['count']
        
        # Status counts
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM candidates
            GROUP BY status
        """)
        status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
        
        # Best score
        cursor.execute("SELECT MAX(score) as best FROM candidates WHERE score IS NOT NULL")
        best_score = cursor.fetchone()['best']
        
        # Average score
        cursor.execute("SELECT AVG(score) as avg FROM candidates WHERE score IS NOT NULL")
        avg_score = cursor.fetchone()['avg']
        
        # Recent candidates (last 24 hours)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM candidates
            WHERE created_at >= datetime('now', '-1 day')
        """)
        recent = cursor.fetchone()['count']
        
        return {
            'total_candidates': total,
            'status_counts': status_counts,
            'best_score': best_score,
            'avg_score': avg_score,
            'recent_candidates': recent
        }
    
    def close(self):
        """Close the database connection"""
        if self.conn:
            self.conn.close()
            logger.debug(f"Closed connection to {self.db_path}")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
