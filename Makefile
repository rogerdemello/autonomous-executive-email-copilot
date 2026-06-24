# Developer convenience targets. Mirrors the CI gates in .github/workflows/ci.yml.
# Usage: `make <target>` (or run the commands directly on Windows PowerShell).

.PHONY: help install lint format test cov typecheck security frontend build run docker check

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime + dev dependencies
	pip install -e ".[dev]"

lint:  ## Ruff lint
	ruff check .

format:  ## Ruff auto-format
	ruff format .

test:  ## Run the test suite
	python -m pytest -q

cov:  ## Run tests with the CI coverage gate
	python -m pytest -q --cov=env --cov=baseline --cov=benchmark --cov=reports --cov=telemetry --cov=server --cov-report=term-missing --cov-fail-under=78

typecheck:  ## Run mypy (informational)
	mypy env --ignore-missing-imports --no-strict-optional

security:  ## Run bandit SAST
	bandit -r env baseline benchmark reports telemetry server -ll

frontend:  ## Lint, test, and build the React dashboard
	cd dashboard && npm ci && npm run lint && npm test && npx tsc --noEmit && npm run build

run:  ## Run the API locally (http://localhost:8000)
	uvicorn env.api:app --reload --port 8000

docker:  ## Build the container image
	docker build -t exec-email-copilot .

check: lint test  ## Lint + test (the fast pre-push gate)
