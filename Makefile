# LoopBench Optimizer — common development commands
#
# Usage: make <target>

PROJECT_DIR := $(shell pwd)
DOCKER_IMAGE := loopbench
VENV_DIR := $(PROJECT_DIR)/.venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip
SRC := openevolve/ loopbench/ sandbox/

.PHONY: help
help:
	@echo "Available targets:"
	@echo "  venv          - Create a virtual environment (.venv)"
	@echo "  install       - Install the package in editable mode"
	@echo "  install-dev   - Install with development dependencies"
	@echo "  lint          - Run ruff linter"
	@echo "  format        - Run ruff formatter"
	@echo "  test          - Run the full test suite (pytest)"
	@echo "  docker-build  - Build the Docker image"
	@echo "  docker-run    - Run the loopbench CLI inside Docker"

.PHONY: all
all: install-dev test

.PHONY: venv
venv:
	python3 -m venv $(VENV_DIR)

.PHONY: install
install: venv
	$(PIP) install -e .

.PHONY: install-dev
install-dev: venv
	$(PIP) install -e ".[dev]"

# Lint with ruff (matches CI)
.PHONY: lint
lint:
	$(PYTHON) -m ruff check $(SRC)

# Auto-format with ruff
.PHONY: format
format:
	$(PYTHON) -m ruff format $(SRC)

# Run the test suite
.PHONY: test
test:
	$(PYTHON) -m pytest

# Build the Docker image
.PHONY: docker-build
docker-build:
	docker build -t $(DOCKER_IMAGE) .

# Run the loopbench CLI inside the container (pass ARGS="run --target . ...")
.PHONY: docker-run
docker-run:
	docker run --rm -v $(PROJECT_DIR):/app --network="host" $(DOCKER_IMAGE) $(ARGS)
