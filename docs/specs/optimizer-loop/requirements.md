# Requirements Document

## Introduction

The OptimizerLoop is an autonomous evolutionary optimization system that integrates all infrastructure components (Repo-to-Context Mapper, Workspace Manager, Docker Sandbox, Candidate Database) into a continuous optimization cycle. This system enables multi-generation evolution of code optimizations similar to AlphaEvolve, where each iteration learns from previous attempts to produce measurably improved code.

The system implements a closed-loop optimization process: it maps repository context, generates optimization patches via LLM, applies patches in isolated worktrees, executes tests in Docker sandboxes, extracts performance metrics, records results in a database, and uses successful optimizations as the foundation for the next generation.

## Glossary

- **OptimizerLoop**: The main orchestrator class that coordinates the autonomous optimization cycle
- **Repo_Mapper**: The Repo-to-Context Mapper component that converts codebases to LLM-friendly prompts
- **Workspace_Manager**: The component that manages git worktrees for isolated patch application
- **Docker_Sandbox**: The isolated execution environment for running tests and extracting metrics
- **Candidate_Database**: The SQLite database storing optimization history and performance data
- **Metric_Parser**: The component that extracts performance numbers from test outputs
- **Generation**: A single iteration of the optimization cycle (map → generate → apply → test → score → record)
- **Candidate**: A single optimization attempt with associated code patch, metrics, and metadata
- **Target_Repository**: The external GitHub repository being optimized
- **Baseline**: The initial candidate representing the unmodified code performance
- **Best_Candidate**: The highest-scoring candidate discovered so far in the optimization run
- **Search_Strategy**: The algorithm determining how to explore the optimization space
- **Optimization_Run**: A complete multi-generation optimization session from start to completion
- **Dashboard**: The visualization interface showing optimization progress over time
- **Audit_Trail**: The complete record of code changes and decisions made during optimization

## Requirements

### Requirement 1: Optimization Cycle Orchestration

**User Story:** As a developer, I want the system to autonomously execute complete optimization cycles, so that code improvements are generated without manual intervention.

#### Acceptance Criteria

1. THE OptimizerLoop SHALL execute the seven-phase cycle: map repository context, generate patch via LLM, apply patch to worktree, run tests in Docker, extract metrics, record to database, and select next baseline
2. WHEN starting a new optimization run, THE OptimizerLoop SHALL establish a baseline by testing the original code and recording its performance metrics
3. WHEN a generation completes successfully, THE OptimizerLoop SHALL advance to the next generation using the best candidate as the new baseline
4. WHEN a generation fails, THE OptimizerLoop SHALL record the failure with error messages and continue to the next generation from the previous best candidate
5. WHEN the maximum iteration count is reached, THE OptimizerLoop SHALL terminate and report the final results
6. FOR ALL optimization runs, THE OptimizerLoop SHALL maintain state consistency between phases (worktree cleanup, database transactions, error recovery)

### Requirement 2: LLM-Driven Patch Generation

**User Story:** As a developer, I want the system to generate optimization patches using LLM understanding of code context, so that improvements are contextually relevant and semantically correct.

#### Acceptance Criteria

1. WHEN generating a patch, THE OptimizerLoop SHALL use the Repo_Mapper to create a context-aware prompt including target file, related files, and performance metrics
2. WHEN sending prompts to the LLM, THE OptimizerLoop SHALL include the current performance baseline and optimization target
3. WHEN the LLM returns a response, THE OptimizerLoop SHALL attempt to parse and extract a valid git patch in unified diff format
4. IF the LLM response lacks valid patch syntax, THEN THE OptimizerLoop SHALL allow extraction to fail, record the failure, and retry generation with clarifying instructions
5. WHEN a previous attempt failed, THE OptimizerLoop SHALL include the error message in the next generation's prompt to guide the LLM away from invalid approaches
6. THE OptimizerLoop SHALL support configurable LLM parameters including model name, temperature, and maximum tokens

### Requirement 3: Isolated Patch Application

**User Story:** As a developer, I want patches applied in isolated worktrees, so that failed optimizations do not corrupt the main repository state.

#### Acceptance Criteria

1. WHEN applying a patch, THE OptimizerLoop SHALL use the Workspace_Manager to create a new git worktree
2. WHEN a worktree is created, THE OptimizerLoop SHALL apply the patch using git apply and verify application success
3. IF patch application fails, THEN THE OptimizerLoop SHALL capture the error output and clean up the worktree
4. WHEN patch application succeeds, THE OptimizerLoop SHALL preserve the worktree for test execution
5. WHEN test execution completes, THE OptimizerLoop SHALL clean up the worktree regardless of test outcome
6. THE OptimizerLoop SHALL ensure no more than the configured maximum number of concurrent worktrees exist at any time

### Requirement 4: Docker-Isolated Test Execution

**User Story:** As a developer, I want tests executed in Docker sandboxes, so that optimizations are validated in consistent, isolated environments.

#### Acceptance Criteria

1. WHEN executing tests, THE OptimizerLoop SHALL use the Docker_Sandbox to run the test suite in an isolated container
2. WHEN starting test execution, THE OptimizerLoop SHALL mount the worktree directory as a read-only volume
3. WHEN tests execute, THE OptimizerLoop SHALL capture both stdout and stderr output streams successfully before proceeding with metric extraction
4. IF either stdout or stderr cannot be captured, THEN THE OptimizerLoop SHALL mark the test execution as failed and record an output stream capture error
5. WHEN tests complete with both streams captured, THE OptimizerLoop SHALL record the exit code, execution time, and output logs
6. IF test execution exceeds the configured timeout, THEN THE OptimizerLoop SHALL terminate the container and record a timeout failure
7. WHEN test execution fails with non-zero exit code, THE OptimizerLoop SHALL preserve error output for feedback to the LLM

### Requirement 5: Performance Metric Extraction

**User Story:** As a developer, I want performance metrics automatically extracted from test outputs, so that optimization improvements are quantified objectively.

#### Acceptance Criteria

1. WHEN tests complete successfully with both output streams captured, THE OptimizerLoop SHALL use the Metric_Parser to extract numeric performance values from output
2. THE OptimizerLoop SHALL support configurable metric extraction patterns including regex patterns and JSON paths
3. WHEN multiple metrics are successfully extracted, THE OptimizerLoop SHALL combine them into a single score using the configured scoring function
4. IF metric extraction fails, THEN THE OptimizerLoop SHALL record the failure and assign a score indicating failure
5. WHEN calculating scores, THE OptimizerLoop SHALL normalize metrics to a consistent scale where higher scores indicate better performance
6. THE OptimizerLoop SHALL support optimization goals including minimize execution time, maximize throughput, minimize memory usage, and custom metrics

### Requirement 6: Candidate Database Management

**User Story:** As a developer, I want all optimization attempts recorded in a database, so that the complete optimization history is preserved and queryable.

#### Acceptance Criteria

1. WHEN a candidate is generated, THE OptimizerLoop SHALL record it to the Candidate_Database with generation number, timestamp, patch content, and parent candidate ID
2. WHEN test execution completes, THE OptimizerLoop SHALL update the candidate record with metrics, score, exit code, and output logs
3. WHEN a candidate fails, THE OptimizerLoop SHALL record failure details including error type, error message, and failure phase
4. THE OptimizerLoop SHALL maintain referential integrity between parent and child candidates forming the optimization lineage
5. WHEN querying optimization history, THE OptimizerLoop SHALL support filtering by generation, score range, success status, and time range
6. WHEN export is requested, THE OptimizerLoop SHALL prepare and export the complete optimization run data in JSON format for external analysis

### Requirement 7: Multi-Generation Evolution

**User Story:** As a developer, I want the system to run for many generations, so that optimizations can compound and discover non-obvious improvements.

#### Acceptance Criteria

1. THE OptimizerLoop SHALL support configurable maximum iteration counts from 1 to 1000 generations
2. WHEN starting generation N+1, THE OptimizerLoop SHALL use the best candidate from generations 0 through N as the baseline
3. WHEN multiple candidates have identical scores, THE OptimizerLoop SHALL select the most recent candidate as the baseline
4. THE OptimizerLoop SHALL track and display progress including current generation, best score so far, and generations remaining
5. WHEN all generations complete, THE OptimizerLoop SHALL produce a final report with optimization summary, best candidate details, and performance improvement percentage
6. WHEN no improvement is observed for the configured patience parameter (number of consecutive generations), THE OptimizerLoop SHALL stop immediately without waiting for the maximum generation count

### Requirement 8: Learning from Failures

**User Story:** As a developer, I want failed optimization attempts to inform future generations, so that the system avoids repeating mistakes.

#### Acceptance Criteria

1. WHEN an optimization fails, THE OptimizerLoop SHALL record the complete error message and failure context
2. WHEN generating the next patch, THE OptimizerLoop SHALL include recent failure messages in the LLM prompt with instructions to avoid similar errors
3. THE OptimizerLoop SHALL maintain a failure history window including the most recent N failures (configurable, default 5)
4. WHEN patch application fails, THE OptimizerLoop SHALL include the git apply error output in the failure feedback
5. WHEN test execution fails, THE OptimizerLoop SHALL include the test error output in the failure feedback
6. WHEN metric extraction fails, THE OptimizerLoop SHALL include the expected metric format in the failure feedback

### Requirement 9: CLI Interface

**User Story:** As a developer, I want a command-line interface to start optimization runs, so that I can easily integrate the system into my workflow.

#### Acceptance Criteria

1. THE CLI SHALL provide a run command accepting target repository URL, metric name, and optional configuration parameters
2. WHEN the run command is invoked, THE CLI SHALL validate all required parameters and display clear error messages for invalid inputs
3. THE CLI SHALL support a --max-iterations parameter specifying the number of generations to execute
4. THE CLI SHALL support a --metric parameter specifying which performance metric to optimize
5. THE CLI SHALL support a --output parameter specifying the directory for storing results and artifacts
6. THE CLI SHALL display real-time progress including current generation, current score, and best score so far
7. WHEN optimization completes, THE CLI SHALL atomically display a summary report and write detailed results to the output directory, ensuring both actions succeed together
8. IF optimization is interrupted or fails partway through, THE CLI SHALL write partial results representing valid intermediate progress to the output directory

### Requirement 10: Real-World Repository Support

**User Story:** As a developer, I want to point the system at actual GitHub repositories, so that I can optimize real code with production constraints.

#### Acceptance Criteria

1. WHEN provided a GitHub repository URL, THE OptimizerLoop SHALL clone the repository to a local working directory
2. WHEN cloning completes, THE OptimizerLoop SHALL detect the repository's primary language and test framework from repository metadata and configuration files
3. THE OptimizerLoop SHALL support repositories requiring authentication via SSH keys or personal access tokens
4. WHEN cloning fails, THE OptimizerLoop SHALL display a clear error message with troubleshooting guidance
5. THE OptimizerLoop SHALL support common Python test frameworks including pytest, unittest, and custom test scripts
6. WHEN a repository uses dependencies, THE OptimizerLoop SHALL ensure dependencies are installed in the Docker environment before running tests

### Requirement 11: Dashboard Visualization

**User Story:** As a developer, I want a visual dashboard showing optimization progress, so that I can monitor the system's performance over time.

#### Acceptance Criteria

1. THE Dashboard SHALL display a line chart with generation number on the x-axis and performance score on the y-axis
2. THE Dashboard SHALL show both individual candidate scores and the best score trajectory over time
3. THE Dashboard SHALL highlight successful candidates in green and failed candidates in red, applying red highlighting logic even in early generations when no failed candidates exist yet
4. THE Dashboard SHALL provide a detailed view for each candidate showing patch content, metrics, and logs when clicked
5. THE Dashboard SHALL auto-refresh at configurable intervals to show live optimization progress
6. THE Dashboard SHALL support exporting charts as PNG images for reporting and documentation

### Requirement 12: Audit Trail

**User Story:** As a developer, I want a complete audit trail of code changes, so that I can understand and reproduce the optimization process.

#### Acceptance Criteria

1. THE OptimizerLoop SHALL record every patch generated with timestamp, generation number, and LLM parameters used
2. THE OptimizerLoop SHALL record the complete prompt sent to the LLM for each generation
3. THE OptimizerLoop SHALL record the complete LLM response including reasoning and patch content
4. THE OptimizerLoop SHALL record all test outputs including stdout, stderr, and exit codes
5. THE OptimizerLoop SHALL record all extracted metrics with the extraction method and raw values
6. THE OptimizerLoop SHALL support exporting the complete audit trail in human-readable markdown format

### Requirement 13: Search Strategy Configuration

**User Story:** As a developer, I want to configure how the system explores the optimization space, so that I can tune the search for different problem types.

#### Acceptance Criteria

1. THE OptimizerLoop SHALL support a greedy search strategy that always uses the single best candidate as baseline
2. THE OptimizerLoop SHALL support a beam search strategy maintaining the top K candidates and exploring from each
3. THE OptimizerLoop SHALL support a random restart strategy that periodically reverts to the baseline to escape local optima
4. THE OptimizerLoop SHALL support configurable exploration parameters including temperature for randomness and beam width for parallel exploration
5. WHEN beam search is active and hardware permits parallelization, THE OptimizerLoop SHALL parallelize test execution across multiple candidates regardless of beam width value
6. WHEN beam search is not active, THE OptimizerLoop SHALL not attempt parallelization even if hardware supports it
7. THE OptimizerLoop SHALL default to greedy search for simplicity and predictability

### Requirement 14: Error Recovery and Robustness

**User Story:** As a developer, I want the system to handle errors gracefully, so that transient failures don't terminate long-running optimization runs.

#### Acceptance Criteria

1. WHEN an LLM API call fails, THE OptimizerLoop SHALL retry with exponential backoff up to a configured maximum retry count
2. WHEN Docker operations fail, THE OptimizerLoop SHALL clean up containers and volumes before retrying
3. WHEN database operations fail, THE OptimizerLoop SHALL roll back the transaction and retry the operation
4. WHEN file system operations fail, THE OptimizerLoop SHALL log the error and attempt cleanup before continuing
5. THE OptimizerLoop SHALL implement a global exception handler that prevents crashes and logs all errors
6. WHEN a critical error occurs that prevents continuation, THE OptimizerLoop SHALL save partial results only when they represent valid intermediate progress before terminating

### Requirement 15: Configuration Management

**User Story:** As a developer, I want optimization runs configured via YAML files, so that I can version control and share optimization settings.

#### Acceptance Criteria

1. THE OptimizerLoop SHALL load configuration from a YAML file specifying all optimization parameters
2. THE Configuration SHALL include all six required sections: repository settings, LLM parameters, Docker settings, database location, metric definitions, and search strategy
3. WHEN loading configuration, THE OptimizerLoop SHALL reject configurations missing any required sections and display clear validation errors
4. THE Configuration SHALL support environment variable substitution for sensitive values like API keys
5. WHEN configuration is invalid, THE OptimizerLoop SHALL display clear validation errors indicating which fields are missing or malformed
6. THE OptimizerLoop SHALL support configuration inheritance where command-line arguments override YAML file values
7. THE OptimizerLoop SHALL generate a default configuration file template when invoked with a --init flag

### Requirement 16: Verified Patch Output

**User Story:** As a developer, I want the final optimized patches validated and ready to apply, so that I can confidently integrate improvements into my codebase.

#### Acceptance Criteria

1. WHEN optimization completes, THE OptimizerLoop SHALL export the best candidate's patch to a file in unified diff format
2. WHEN verifying patches, THE OptimizerLoop SHALL attempt to apply them cleanly to the original repository and report validation as 'failed' when conflicts are detected, reserving 'passed' only for patches that apply without issues
3. THE OptimizerLoop SHALL include a validation report showing before/after metrics and the percentage improvement
4. THE OptimizerLoop SHALL generate a README explaining the optimization approach and listing all files modified
5. THE OptimizerLoop SHALL support generating a pull request description summarizing the changes and performance gains
6. WHEN the confidence score is strictly below the configured threshold, THE OptimizerLoop SHALL warn about low confidence or marginal improvement over baseline

### Requirement 17: Performance Optimization Target Use Cases

**User Story:** As a developer, I want the system optimized for real-world performance bottlenecks, so that it produces meaningful improvements on actual code.

#### Acceptance Criteria

1. THE OptimizerLoop SHALL successfully optimize JSON validators by reducing parsing and validation time
2. THE OptimizerLoop SHALL successfully optimize math libraries by reducing computational complexity
3. THE OptimizerLoop SHALL successfully optimize string utilities by improving algorithm efficiency
4. THE OptimizerLoop SHALL target measurable improvements of at least 10% on the target metric as the success threshold for optimization runs
5. THE OptimizerLoop SHALL differentiate between "Completed" runs (finished without crashing) and "Successful" runs (achieved improvement exceeding the configured success threshold)
6. WHEN an optimization run achieves less than the configured success threshold, THE OptimizerLoop SHALL mark the run as "Completed" but not "Successful" in the final report
7. THE OptimizerLoop SHALL complete optimization runs on repositories up to 10,000 lines of code within 24 hours on standard hardware
8. THE OptimizerLoop SHALL support custom benchmark scripts allowing users to define domain-specific performance tests
