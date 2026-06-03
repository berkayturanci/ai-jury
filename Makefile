.PHONY: help install test live-smoke smoke benchmark lint format coverage build assets clean

help:
	@echo "Available commands:"
	@echo "  make install    - Install the package in editable mode with development tools"
	@echo "  make test       - Run the offline unit test suite"
	@echo "  make live-smoke - Run opt-in live native-CLI smoke tests (JURY_LIVE=1)"
	@echo "  make smoke      - Run the mock CLI smoke test"
	@echo "  make benchmark  - Run the offline review-quality benchmark (JURY_BENCH_LIVE=1 for live)"
	@echo "  make lint       - Run Ruff checks"
	@echo "  make format     - Format Python code with Ruff"
	@echo "  make coverage   - Measure test coverage and enforce the minimum gate"
	@echo "  make build      - Build sdist and wheel packages"
	@echo "  make assets     - Re-render website/docs PNGs from their SVG sources (needs rsvg-convert)"
	@echo "  make clean      - Remove build artifacts and Python caches"

install:
	python3 -m pip install --upgrade pip
	python3 -m pip install -e ".[dev]"

test:
	python3 -m unittest discover -s tests -v

# Opt-in live smoke tests: exercise the real native agent CLIs (claude, codex,
# agy) end to end. Skipped entirely unless JURY_LIVE=1; each agent whose CLI
# is not on PATH is skipped individually. Intentionally excluded from CI.
live-smoke:
	JURY_LIVE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v

smoke:
	PYTHONPATH=src python3 -m ai_jury --mock --diff-file examples/sample.diff -q

# Offline jury-review-quality benchmark (issue #12). Scores each fixture's
# recorded findings against its expected spec; deterministic, no live CLIs, no
# network. Set JURY_BENCH_LIVE=1 to instead run the real jury per fixture
# diff and score the live output (opt-in; never in CI). Small and directional —
# not a universal quality claim. See benchmark/README.md.
benchmark:
	PYTHONPATH=src python3 -m ai_jury.benchmark

lint:
	ruff check .

format:
	ruff format .

# Run the suite under coverage and enforce the minimum gate (fail_under in
# pyproject.toml [tool.coverage.report]). Also writes an HTML report to htmlcov/.
coverage:
	python3 -m coverage run -m unittest discover -s tests
	python3 -m coverage report
	python3 -m coverage html

build:
	python3 -m build --sdist --wheel --outdir dist/

# Re-render the committed raster assets from their SVG sources so the PNGs can
# never drift from the vector originals (favicons + README hero are authored
# as SVG). Requires librsvg: `brew install librsvg` / `apt install librsvg2-bin`
# (provides rsvg-convert). The OG banner is a one-shot designer PNG (no SVG
# source ships in-tree) and is intentionally not rebuilt here.
assets:
	rsvg-convert -w 2400 -h 1260 docs/assets/hero.svg       -o docs/assets/hero.png
	rsvg-convert -w 2400 -h 1260 docs/assets/hero-light.svg -o docs/assets/hero-light.png
	rsvg-convert -w 180  -h 180  website/favicon.svg  -o website/apple-touch-icon.png
	rsvg-convert -w 180  -h 180  website/favicon.svg  -o website/favicon-180.png
	rsvg-convert -w 32   -h 32   website/favicon.svg  -o website/favicon-32.png
	rsvg-convert -w 16   -h 16   website/favicon.svg  -o website/favicon-16.png

clean:
	rm -rf build/ dist/ *.egg-info/ src/*.egg-info/ .ruff_cache/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
