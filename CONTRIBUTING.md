# Contributing to LoopBench Optimizer

Thanks for your interest in contributing to LoopBench Optimizer! This document
covers how to set up a development environment, run the tests, and submit
changes.

## Getting Started

1. Fork the repository on GitHub.
2. Clone your fork:
   ```bash
   git clone https://github.com/manashatwar/LoopBench-Optimizer.git
   cd LoopBench-Optimizer
   ```
3. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   ```
4. Install the package in development mode:
   ```bash
   pip install -e ".[dev]"
   ```
5. Run the test suite to confirm everything works:
   ```bash
   python -m pytest
   ```

**Note:** The unit tests do not make real LLM API calls. Some tests expect the
`OPENAI_API_KEY` environment variable to be set to any non-empty value (e.g.
`test-key`). Set it before running the suite if needed.

## LLM Configuration

LoopBench talks to any OpenAI-compatible endpoint. For local development and the
`loopbench run` hero command, configure credentials in a `.env` file at the repo
root:

```
GEMINI_API_KEY="your-api-key"
LLM_API_BASE="https://api.groq.com/openai/v1"
LLM_MODEL="llama-3.3-70b-versatile"
```

The variable name `GEMINI_API_KEY` is used regardless of provider; point
`LLM_API_BASE` at whichever OpenAI-compatible service you use (Groq, OpenAI,
etc.).

## Code Style

The project uses [ruff](https://docs.astral.sh/ruff/) for both linting and
formatting. Before submitting a change:

```bash
ruff check openevolve/ loopbench/ sandbox/
ruff format openevolve/ loopbench/ sandbox/
```

A pre-commit hook is provided to run these automatically:

```bash
pre-commit install
```

## Pull Request Process

1. Create a branch for your change:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. Make your changes and add tests that cover them.
3. Run the linter and the full test suite:
   ```bash
   ruff check openevolve/ loopbench/ sandbox/
   python -m pytest
   ```
4. Commit with a clear, descriptive message.
5. Push to your fork and open a pull request against `main`.

## Reporting Issues

When reporting an issue, please include:

1. A clear description of the problem.
2. Steps to reproduce.
3. Expected vs. actual behavior.
4. Environment details (OS, Python version).

## Feature Requests

Feature requests are welcome. Please describe the feature, the motivation behind
it, and any implementation ideas you have.

## Code of Conduct

Please be respectful and considerate of others. We aim to keep this a welcoming
and inclusive project for everyone.
