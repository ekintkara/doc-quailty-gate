.PHONY: install install-dev lint test test-cov smoke demo run-proxy review clean

PYTHON ?= python3
PIP ?= pip3

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

lint:
	ruff check src/ tests/ --fix

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=app --cov-report=term-missing

run-proxy:
	litellm --config config/litellm/config.yaml --port 4000

smoke:
	dqg smoke-test

demo:
	dqg demo

review:
	@$(PYTHON) -m app.cli review --help

clean:
	rm -rf outputs/runs/*
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
