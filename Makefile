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
	$(PYTHON) -c "import shutil, pathlib; [shutil.rmtree(str(p)) for p in pathlib.Path('.').rglob('__pycache__') if p.is_dir()]"
	$(PYTHON) -c "import pathlib; [p.unlink() for p in pathlib.Path('.').rglob('*.pyc')]"
	$(PYTHON) -c "import shutil, pathlib; d=pathlib.Path('outputs/runs'); [shutil.rmtree(str(p)) for p in d.iterdir() if p.is_dir()] if d.exists() else None"
