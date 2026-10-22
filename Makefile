# convox developer commands. `make help` lists them.
# Extra arguments: make bench BENCH_ARGS="--pair hi-kn", make smoke SMOKE_ARGS="--only mt",
# make label TURNS=out/turns.json RATER=asha.

.DEFAULT_GOAL := help
.PHONY: help install test test-live lint fmt dev up down bench smoke fixtures label

HOST ?= 127.0.0.1
PORT ?= 8000
BENCH_ARGS ?=
SMOKE_ARGS ?=
TURNS ?= out/turns.json
RATER ?=

help: ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## install deps (incl. dev group) into .venv
	uv sync

test: ## offline tests (live vendor tests are deselected)
	uv run pytest -q

test-live: ## tests that hit real vendors; needs keys in .env
	uv run pytest -m live -s

lint: ## ruff lint + format check (what CI runs)
	uv run ruff check .
	uv run ruff format --check .

fmt: ## autofix lint and format
	uv run ruff check --fix .
	uv run ruff format .

dev: ## API on :8000 with reload (one worker: both legs of a call share a process)
	uv run uvicorn convox.api.app:app --host $(HOST) --port $(PORT) --reload --ws-per-message-deflate false

up: ## start postgres + redis (docker compose)
	docker compose up -d postgres redis

down: ## stop the compose stack
	docker compose down

bench: ## offline pipeline bench (tools/bench.py)
	uv run python -m tools.bench $(BENCH_ARGS)

smoke: ## live vendor smoke test with .env keys; wavs to out/smoke/
	uv run python tools/smoke.py $(SMOKE_ARGS)

fixtures: ## regenerate synthetic test wavs in tests/fixtures/
	uv run python tools/gen_fixtures.py

label: ## meaning-rating page for a turn log: make label TURNS=... RATER=...
	uv run python tools/label.py $(TURNS) --rater "$(RATER)"
