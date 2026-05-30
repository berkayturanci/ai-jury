.PHONY: help install test smoke lint format build clean

help:
	@echo "Available commands:"
	@echo "  make install  - Install the package in editable mode with development tools"
	@echo "  make test     - Run the offline unit test suite"
	@echo "  make smoke    - Run the mock CLI smoke test"
	@echo "  make lint     - Run Ruff checks"
	@echo "  make format   - Format Python code with Ruff"
	@echo "  make build    - Build sdist and wheel packages"
	@echo "  make clean    - Remove build artifacts and Python caches"

install:
	python3 -m pip install --upgrade pip
	python3 -m pip install -e .
	python3 -m pip install build pre-commit ruff

test:
	python3 -m unittest discover -s tests -v

smoke:
	PYTHONPATH=src python3 -m agent_review_council --mock --diff-file examples/sample.diff -q

lint:
	ruff check .

format:
	ruff format .

build:
	python3 -m build --sdist --wheel --outdir dist/

clean:
	rm -rf build/ dist/ *.egg-info/ src/*.egg-info/ .ruff_cache/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
