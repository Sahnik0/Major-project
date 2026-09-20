.PHONY: dev dev-api dev-web test lint seed up up-cpu clean

# Virtualenv python/uv wrappers
PYTHON ?= .venv/bin/python
UVICORN ?= .venv/bin/uvicorn
PYTEST ?= .venv/bin/pytest
RUFF ?= .venv/bin/ruff

dev:
	@echo "Starting API on http://localhost:8000 (run 'make dev-web' in a separate terminal for Next.js)"
	$(UVICORN) apps.api.app.main:app --reload --host 0.0.0.0 --port 8000

dev-api:
	$(UVICORN) apps.api.app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd apps/web && npm run dev

test:
	$(PYTEST) apps/api/tests -v

lint:
	uv run ruff check apps/ scripts/

format:
	uv run ruff format apps/ scripts/

seed:
	$(PYTHON) scripts/generate_sample_data.py

up:
	docker compose up --build

up-cpu:
	docker compose --profile cpu up --build

clean:
	rm -rf data/uploads/* data/parquet/* data/sqlite/* .pytest_cache
	touch data/uploads/.gitkeep data/parquet/.gitkeep data/sqlite/.gitkeep
