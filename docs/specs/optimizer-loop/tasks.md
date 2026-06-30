# Implementation Plan: OptimizerLoop

## Overview

This implementation plan extends the OpenEvolve fork to build the OptimizerLoop autonomous evolutionary optimization system. **OpenEvolve already provides ~65% of the required infrastructure**, including WorkspaceManager (95% complete), RepoMapper (98% complete), MetricParser (90% complete), and LLM integration (80% complete).

This plan focuses on:
- **Extending** existing OpenEvolve components (database, Docker sandbox, LLM client)
- **Integrating** components into a 7-phase orchestration cycle
- **Building** new CLI commands, dashboard, and reporting features

The implementation follows an integration-first approach: verify existing components, extend where needed, wire into orchestration layer, then add user-facing features.

## Tasks

- [x] 1. Verify and extend CandidateDatabase (OpenEvolve: 70% complete)
  - [x] 1.1 Extend openevolve/database.py with SQLite audit tables
    - Add SQLAlchemy models for `runs` and `audit_log` tables to existing `ProgramDatabase` class
    - Keep existing JSON-based `Program` storage, add relational tables for runs tracking
    - Implement connection management with transaction support and rollback on error
    - Create database initialization that sets up new tables alongside existing structure
    - _Requirements: 6.1, 6.2, 6.4_
  
  - [x]* 1.2 Write property test for database referential integrity
    - **Property 7: Database Referential Integrity**
    - **Validates: Requirements 6.4**
    - Generate random candidate insertions with parent_id references
    - Verify all parent_id values either reference existing candidates or are NULL (baseline candidates)
    - Test that foreign key constraints prevent orphaned references
  
  - [x] 1.3 Add export and failure tracking methods
    - Extend `ProgramDatabase` with `export_run()` method that generates JSON export
    - Add `get_recent_failures()` method with configurable window size for LLM feedback
    - Ensure methods work with both existing Program storage and new runs/audit tables
    - Include all candidates, metrics, patches, and audit trail in export
    - _Requirements: 6.5, 6.6_

- [x] 2. Verify WorkspaceManager integration (OpenEvolve: 95% complete)
  - [x]* 2.1 Verify openevolve/workspace_manager.py meets requirements
    - Read existing WorkspaceManager implementation
    - Verify worktree creation, patch application, and cleanup methods exist
    - Test with sample patches to ensure git apply validation works
    - Confirm signal handlers (SIGTERM/SIGINT) for cleanup are present
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.6_
  
  - [x]* 2.2 Write property tests for worktree cleanup and patch validation
    - **Property 4: Patch Validation Status Assignment**
    - **Validates: Requirements 16.2**
    - Generate valid and invalid patches
    - Verify system reports 'passed' only when patch applies cleanly without conflicts
    - **Property 8: Worktree Cleanup Invariant**
    - **Validates: Requirements 3.5**
    - Simulate successful and failed test executions
    - Verify worktree is always cleaned up after execution completes

- [ ] 3. Extend DockerSandbox with output stream verification (OpenEvolve: 75% complete)
  - [ ] 3.1 Add output stream verification to sandbox/runner.py
    - Modify `run_in_sandbox()` to capture stdout and stderr separately (not just JSON score)
    - Add `verify_output_streams()` function that checks both streams are non-None
    - Return error dict with 'output_capture_failed' status if either stream missing
    - Ensure verification happens BEFORE metric extraction attempts
    - _Requirements: 4.3, 4.4_
  
  - [ ]* 3.2 Write property tests for output stream capture
    - **Property 1: Output Stream Capture Completeness**
    - **Validates: Requirements 4.3**
    - Simulate test executions that produce both stdout and stderr
    - Verify both streams are successfully captured before metric extraction proceeds
    - **Property 2: Output Stream Capture Error Handling**
    - **Validates: Requirements 4.4**
    - Simulate scenarios where stdout or stderr capture fails
    - Verify system marks test as failed and records output capture error
  
  - [ ]* 3.3 Verify Docker timeout and cleanup (already in OpenEvolve)
    - Test existing timeout handling (default 120s)
    - Verify container cleanup happens regardless of test outcome
    - Test image building and caching functionality
    - _Requirements: 4.1, 4.2, 4.5, 4.6_

- [x] 4. Verify MetricParser integration (OpenEvolve: 90% complete)
  - [x]* 4.1 Verify openevolve/metric_parser.py meets requirements
    - Read existing MetricParser implementation
    - Test regex pattern extraction with sample outputs
    - Verify metric normalization (scale factors) works correctly
    - Confirm multi-metric aggregation functionality
    - _Requirements: 5.1, 5.2, 5.3, 5.6_
  
  - [x]* 4.2 Write property test for metric extraction ordering constraint
    - **Property 9: Metric Extraction Ordering Constraint**
    - **Validates: Requirements 5.1**
    - Test various combinations of stdout/stderr availability
    - Verify extraction only proceeds when both streams are successfully captured
    - Verify extraction is prevented with incomplete output data

- [x] 5. Integrate RepoMapper (OpenEvolve: 98% complete)
  - [x]* 5.1 Verify openevolve/repo_mapper/ module meets requirements
    - Read existing RepoMapper implementation (mapper.py, scanner.py, import_analyzer.py)
    - Test repository traversal and dependency discovery
    - Verify context building and prompt generation work
    - Confirm caching functionality for performance
    - _Requirements: 2.1_
  
  - [x] 5.2 Configure prompt template to match design specification
    - Modify openevolve/repo_mapper/context_builder.py prompt template
    - Include target file contents, baseline metrics, optimization goal
    - Add failure history section for LLM feedback integration
    - Use template format from design document
    - _Requirements: 2.2, 2.5_

- [x] 6. Extend LLMClient with patch extraction (OpenEvolve: 80% complete)
  - [x] 6.1 Add patch extraction methods to openevolve/llm/base.py
    - Extend `LLMInterface` with `extract_patch_from_response()` method
    - Look for code blocks marked as `diff` or `patch` using regex
    - Validate unified diff format (starts with `---` and `+++`)
    - Return None if no valid patch found
    - _Requirements: 2.3, 2.4_
  
  - [x] 6.2 Add patch generation with retry to LLM ensemble
    - Extend `LLMEnsemble` with `generate_patch()` method that calls providers
    - Implement `retry_with_clarification()` for parse failures
    - Max retries: 3 with exponential backoff (1s, 2s, 4s)
    - Leverage existing retry logic and provider switching in OpenEvolve
    - _Requirements: 2.5, 14.1_
  
  - [x]* 6.3 Verify LLM provider integration (already in OpenEvolve)
    - Test existing OpenAI, Anthropic, and Ollama providers
    - Verify retry logic with exponential backoff works
    - Test timeout handling and error recovery
    - _Requirements: 2.6, 14.1_

- [x] 7. Checkpoint - Ensure all component extensions pass tests
  - Run all unit tests and property tests for extended components
  - Verify database exports work correctly
  - Verify Docker output verification prevents metric extraction on failures
  - Verify RepoMapper produces prompts with failure history
  - Verify LLMClient extracts patches from responses
  - Ensure all tests pass, ask the user if questions arise


- [x] 8. Build SearchStrategy abstraction layer (OpenEvolve: 60% complete)
  - [x] 8.1 Create SearchStrategy abstract base class
    - Write `openevolve/search_strategy.py` with abstract `SearchStrategy` class
    - Define interface: `select_baseline()`, `should_parallelize()`
    - _Requirements: 13.1, 13.7_
  
  - [x] 8.2 Implement GreedySearch strategy
    - Create `GreedySearch` class that always selects best candidate
    - Implement `select_baseline()` to return max score candidate
    - Return False for `should_parallelize()`
    - _Requirements: 13.1, 13.7_
  
  - [x] 8.3 Implement BeamSearch strategy wrapper
    - Create `BeamSearch` class with configurable beam_width
    - Implement `select_baseline()` to randomly choose from top-K candidates
    - Return True for `should_parallelize()` when beam width > 1
    - Optionally wrap existing MAP-Elites logic from openevolve/process_parallel.py
    - _Requirements: 13.2, 13.5, 13.6_
  
  - [x] 8.4 Implement RandomRestartSearch strategy
    - Create `RandomRestartSearch` class with configurable restart_interval
    - Implement `select_baseline()` to periodically revert to original baseline
    - Return False for `should_parallelize()`
    - _Requirements: 13.3, 13.7_
  
  - [x] 8.5 Create strategy factory
    - Write `create_strategy()` function that instantiates strategy from config
    - Support 'greedy', 'beam', and 'random_restart' strategy types
    - _Requirements: 13.4_
  
  - [x]* 8.6 Write unit tests for search strategies
    - Test greedy selection with mock candidate histories
    - Test beam search with various beam widths
    - Test random restart interval timing
    - Test strategy factory with different configs

- [x] 9. Extend ConfigManager (OpenEvolve: 75% complete)
  - [x] 9.1 Add configuration validation to openevolve/config.py
    - Extend existing `Config` dataclass with validation method
    - Write `validate_config()` that checks all 6 required sections present
    - Verify sections: repository, llm, docker, database, metrics, search
    - Validate field types and ranges
    - _Requirements: 15.2, 15.3, 15.5_
  
  - [x]* 9.2 Write property test for configuration validation completeness
    - **Property 3: Configuration Validation Completeness**
    - **Validates: Requirements 15.2, 15.3**
    - Generate configurations with various missing sections
    - Verify system accepts only when all 6 sections present
    - Verify system rejects with clear errors when any section missing
  
  - [x] 9.3 Add configuration template generation
    - Write `generate_template()` function that creates default YAML file
    - Include all 6 required sections with example values
    - Add comments explaining each field
    - Leverage existing OpenEvolve config structure
    - _Requirements: 15.7_
  
  - [x]* 9.4 Verify CLI argument merging (already in OpenEvolve)
    - Test existing CLI argument override functionality
    - Verify max_iterations, patience, and other parameters can be overridden
    - _Requirements: 15.6_

- [x] 10. Build OptimizerLoop orchestrator
  - [x] 10.1 Create 7-phase orchestrator (refactor openevolve/controller.py OR create new)
    - Create `openevolve/optimizer_loop.py` with `OptimizerLoop` class
    - Initialize all components: RepoMapper, LLMClient, WorkspaceManager, DockerSandbox, MetricParser, Database, SearchStrategy
    - Use existing OpenEvolve components where possible
    - _Requirements: 1.1_
  
  - [x] 10.2 Implement baseline establishment
    - Write `establish_baseline()` method that tests original code
    - Create baseline candidate with generation=0 and parent_id=None
    - Use existing DockerSandbox and MetricParser from OpenEvolve
    - Record baseline metrics to database
    - _Requirements: 1.2_
  
  - [x] 10.3 Implement single generation execution (7-phase cycle)
    - Write `execute_generation()` method implementing the seven-phase cycle
    - Phase 1: Map repository context using RepoMapper (OpenEvolve)
    - Phase 2: Generate patch using LLMClient (OpenEvolve + patch extraction extension)
    - Phase 3: Apply patch using WorkspaceManager (OpenEvolve)
    - Phase 4: Execute tests using DockerSandbox (OpenEvolve + output verification extension)
    - Phase 5: Extract metrics using MetricParser (OpenEvolve)
    - Phase 6: Record to database using CandidateDatabase (OpenEvolve + extension)
    - Phase 7: Handle failures at each phase and record failure details
    - _Requirements: 1.1, 1.4_
  
  - [x] 10.4 Implement multi-generation loop with early stopping
    - Write `run()` method that executes generations from 1 to max_iterations
    - Track best candidate and generations without improvement
    - Update baseline after each generation using search strategy
    - Add early stopping check: terminate when generations_without_improvement >= patience
    - _Requirements: 1.3, 7.1, 7.2, 7.3, 7.6_
  
  - [x]* 10.5 Write property test for early stopping trigger precision
    - **Property 5: Early Stopping Trigger Precision**
    - **Validates: Requirements 7.6**
    - Simulate optimization runs with configured patience values
    - Verify system terminates immediately after exactly P consecutive generations without improvement
    - Verify termination happens before max generation count is reached
  
  - [x] 10.6 Implement error recovery and resource cleanup
    - Add try-except blocks around generation execution
    - Implement cleanup on critical errors
    - Save partial results on failure
    - Ensure worktrees cleaned up in all scenarios
    - Leverage OpenEvolve's existing error handling where applicable
    - _Requirements: 1.6, 14.2, 14.3, 14.4, 14.5, 14.6_
  
  - [x]* 10.7 Write integration tests for orchestrator
    - Test complete optimization run with mock components
    - Test early stopping triggers correctly
    - Test error recovery and cleanup
    - Test state consistency across generations

- [x] 11. Implement final report generation
  - [x] 11.1 Create report generation logic
    - Write `generate_final_report()` method in OptimizerLoop
    - Calculate improvement percentage comparing best to baseline
    - Determine status: 'successful' if improvement > threshold, else 'completed'
    - Include best candidate details, patches, and metrics
    - _Requirements: 7.5, 17.4, 17.5, 17.6_
  
  - [x]* 11.2 Write property test for success threshold differentiation
    - **Property 6: Success Threshold Differentiation**
    - **Validates: Requirements 17.5, 17.6**
    - Test with various improvement percentages and threshold values
    - Verify 'Successful' status only when improvement exceeds threshold
    - Verify 'Completed' status when improvement <= threshold
  
  - [x] 11.3 Implement verified patch output
    - Write patch file in unified diff format
    - Generate validation report with before/after metrics
    - Create README explaining optimization approach
    - Generate PR description template
    - Warn when confidence below threshold
    - _Requirements: 16.1, 16.3, 16.4, 16.5, 16.6_
  
  - [x]* 11.4 Write unit tests for report generation
    - Test improvement calculation
    - Test status determination based on threshold
    - Test patch export formatting
    - Test validation report content

- [x] 12. Checkpoint - Ensure orchestrator integration works end-to-end
  - Run integration tests for complete optimization cycle
  - Test with mock repository and LLM responses
  - Verify all phases execute in correct order
  - Verify OpenEvolve components integrate correctly
  - Ensure all tests pass, ask the user if questions arise

- [x] 13. Extend CLI interface (OpenEvolve: 40% complete)
  - [x] 13.1 Add init command to openevolve/cli.py
    - Extend existing CLI with `init` subcommand
    - Accept --output parameter for file location
    - Use ConfigManager.generate_template() from Task 9.3
    - _Requirements: 15.7_
  
  - [x] 13.2 Enhance run command
    - Extend existing `run` command to use OptimizerLoop orchestrator
    - Accept --config, --max-iterations, --metric, --output parameters
    - Validate required parameters and display errors
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_
  
  - [x] 13.3 Implement enhanced progress display
    - Write progress display that updates during optimization
    - Show current generation, current score, best score, time elapsed, ETA
    - Show recent candidates with success/failure indicators
    - _Requirements: 9.6_
  
  - [x] 13.4 Implement atomic output on completion
    - Display summary report and write results as atomic operation
    - Ensure both succeed together
    - Write valid intermediate progress on partial failure
    - _Requirements: 9.7, 9.8_
  
  - [x] 13.5 Add resume command
    - Create `resume` subcommand that continues from checkpoint
    - Accept --run-id parameter
    - Load state from database and continue optimization
    - _Requirements: 9.1_
  
  - [x] 13.6 Add export command
    - Create `export` subcommand that exports optimization run
    - Accept --run-id and --format parameters
    - Use CandidateDatabase.export_run() from Task 1.3
    - _Requirements: 9.1_
  
  - [x]* 13.7 Write integration tests for CLI commands
    - Test init command creates valid config file
    - Test run command with valid and invalid parameters
    - Test progress display updates correctly
    - Test resume and export commands

- [x] 14. Implement audit trail functionality
  - [x] 14.1 Create audit logging in database extension
    - Extend database from Task 1.1 with `log_event()` method
    - Record events to audit_log table with timestamps
    - Support event types: generation_start, patch_generated, test_executed, etc.
    - Leverage existing OpenEvolve evolution_trace.py lineage tracking
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_
  
  - [x] 14.2 Implement audit export
    - Write `export_audit_trail()` method that generates markdown report
    - Include all patches, prompts, responses, and metrics
    - Format as human-readable document
    - _Requirements: 12.6_
  
  - [x]* 14.3 Write unit tests for audit trail
    - Test event logging
    - Test audit export formatting
    - Test audit query filtering

- [x] 15. Build Dashboard web interface (two-mode: static GitHub Pages + live local)
  - [x] 15.1 Create static dashboard (GitHub Pages compatible)
    - Write `docs/index.html` — single-file dashboard, zero build step required
    - Load Recharts + React from CDN (no npm/webpack needed)
    - In static mode: fetch `data.json` from same directory (GitHub Pages)
    - In live mode: poll `/api/runs/:id` on localhost every few seconds
    - Auto-detect which mode to use based on `window.location`
    - _Requirements: 11.1, 11.2, 11.3, 11.4_
  
  - [x] 15.2 Implement chart components in dashboard
    - Line chart: generation number (x-axis) vs score (y-axis)
    - Dual lines: individual candidate scores + best score trajectory
    - Scatter markers: green for successful, red for failed candidates
    - Apply red highlighting consistently even in early generations
    - _Requirements: 11.1, 11.2, 11.3_
  
  - [x] 15.3 Implement candidate detail view
    - Click any candidate dot/row to show detail panel
    - Display: patch content (syntax highlighted), metrics table, stdout/stderr logs
    - Show failure reason when candidate failed
    - _Requirements: 11.4_
  
  - [x] 15.4 Implement auto-refresh (polling-based, GitHub Pages safe)
    - Configurable interval via URL param `?refresh=5` (seconds)
    - In live mode: re-fetch `/api/runs/:id` at interval
    - In static mode: no-op (data.json is a snapshot)
    - Show last-updated timestamp in UI
    - _Requirements: 11.5_
  
  - [x] 15.5 Implement chart export (client-side PNG)
    - Export charts as PNG using Canvas API (no server needed)
    - Works on both GitHub Pages and local modes
    - _Requirements: 11.6_
  
  - [x] 15.6 Add dashboard CLI command + data.json generator
    - Add `dashboard` subcommand to `openevolve/cli.py` (optimizer_main)
    - `--run-id`: export run to `docs/data.json` for GitHub Pages
    - `--port`: start lightweight local HTTP server serving `docs/`
    - `--open`: auto-open browser
    - Write `optimizer export-dashboard --run-id abc123` → generates `docs/data.json`
    - _Requirements: 9.1_
  
  - [x]* 15.7 Write integration tests for dashboard
    - Test data.json generation from a real run
    - Test static HTML contains all required sections
    - Test CLI dashboard command creates correct output files
    - Test candidate detail data structure

- [x] 16. Implement repository support (OpenEvolve: 50% complete)
  - [x] 16.1 Formalize repository cloning
    - Create `openevolve/repo_manager.py` module
    - Implement `clone_repository()` method with HTTPS and SSH support
    - Support authentication via tokens and SSH keys
    - Build on existing OpenEvolve examples that use cloning
    - _Requirements: 10.1, 10.3_
  
  - [x] 16.2 Implement repository metadata detection
    - Write `detect_language()` method that identifies primary language
    - Write `detect_test_framework()` method that finds test configuration
    - Support Python projects with pytest, unittest, custom scripts
    - Leverage existing OpenEvolve code_utils.py language detection
    - _Requirements: 10.2, 10.5_
  
  - [x] 16.3 Implement dependency installation
    - Ensure dependencies installed in Docker environment
    - Parse requirements.txt, setup.py, or pyproject.toml
    - Add dependency installation to Dockerfile build process
    - _Requirements: 10.6_
  
  - [x]* 16.4 Write integration tests for repository support
    - Test cloning public and private repositories
    - Test language and framework detection
    - Test dependency installation
    - Test error handling for invalid repos

- [x] 17. Final integration and end-to-end testing
  - [x] 17.1 Write end-to-end test with real repository
    - Create test that runs complete optimization on sample repository
    - Verify all phases execute correctly using OpenEvolve components
    - Verify results written to database
    - Verify final report generated
    - _Requirements: 17.1, 17.2, 17.3_
  
  - [x]* 17.2 Write performance tests
    - Test optimization completes within 24 hours for 10,000 LOC repositories
    - Test memory usage stays within acceptable bounds
    - Test database query performance with large candidate counts
    - _Requirements: 17.7_
  
  - [x] 17.3 Test custom benchmark scripts
    - Test system with user-defined performance tests
    - Verify custom metric extraction patterns work
    - Verify custom scoring functions work
    - _Requirements: 17.8_

- [x] 18. Final checkpoint - Complete system verification
  - Run all tests including unit, integration, property-based, and end-to-end tests
  - Verify CLI commands work correctly
  - Verify dashboard displays optimization progress
  - Verify complete audit trail is generated
  - Test with real GitHub repositories
  - Verify OpenEvolve components integrate seamlessly
  - Ensure all tests pass, ask the user if questions arise

## Notes

- Tasks marked with `*` are optional test-related sub-tasks that can be skipped for faster MVP delivery
- Each task references specific requirements for traceability
- **This implementation extends the OpenEvolve fork** - approximately 65% of infrastructure already exists
- **OpenEvolve provides**: WorkspaceManager (95%), RepoMapper (98%), MetricParser (90%), LLM integration (80%), Database foundation (70%), Config management (75%)
- **We're building**: Output stream verification, patch extraction, SearchStrategy abstraction, 7-phase orchestrator, CLI commands, dashboard, reporting
- The implementation focuses on integration and extension rather than building from scratch
- Property tests validate universal correctness properties defined in the design document
- Unit tests validate specific examples and edge cases
- Integration tests verify OpenEvolve components work together correctly
- End-to-end tests verify the complete system works with real repositories
- Checkpoints ensure incremental validation at major milestones
- The system is designed for extensibility: new LLM providers, search strategies, and metric extractors can be added by implementing the appropriate interfaces

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "4.1", "5.1", "6.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2", "3.2", "4.2", "5.2", "6.2", "8.1"] },
    { "id": 2, "tasks": ["3.3", "6.3", "8.2", "8.3", "8.4", "9.1", "9.2"] },
    { "id": 3, "tasks": ["8.5", "8.6", "9.3", "9.4", "10.1"] },
    { "id": 4, "tasks": ["10.2"] },
    { "id": 5, "tasks": ["10.3"] },
    { "id": 6, "tasks": ["10.4", "10.5"] },
    { "id": 7, "tasks": ["10.6", "10.7", "11.1", "11.2", "14.1"] },
    { "id": 8, "tasks": ["11.3", "11.4", "14.2", "16.1"] },
    { "id": 9, "tasks": ["13.1", "13.2", "16.2", "16.3"] },
    { "id": 10, "tasks": ["13.3", "13.4", "16.4"] },
    { "id": 11, "tasks": ["13.5", "13.6", "13.7", "15.1"] },
    { "id": 12, "tasks": ["15.2", "15.3"] },
    { "id": 13, "tasks": ["15.4", "15.5"] },
    { "id": 14, "tasks": ["15.6", "15.7"] },
    { "id": 15, "tasks": ["17.1", "17.2", "17.3"] }
  ]
}
```
