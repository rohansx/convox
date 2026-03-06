.PHONY: help up down logs install build clean \
        db-up db-down db-reset migrate-up migrate-down migrate-status migrate-new \
        api-dev api-build api-test web-dev web-build web-check fmt lint

# --- Quick start -------------------------------------------------------
help: ## Show all commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

up: db-up api-dev web-dev ## Start everything (DB + API + frontend)

down: ## Stop all servers
	@kill $$(cat .pid.api 2>/dev/null) 2>/dev/null || true
	@kill $$(cat .pid.web 2>/dev/null) 2>/dev/null || true
	@rm -f .pid.api .pid.web
	@echo "Servers stopped"

logs: ## Tail logs from both servers
	@tail -f /tmp/convox-api.log /tmp/convox-web.log

# --- Dependencies -------------------------------------------------------
install: ## Install all dependencies
	cd api && uv sync
	cd web && bun install

# --- Database ------------------------------------------------------------
DB_CONTAINER := convox-postgres
DB_URL := postgres://convox:convox@localhost:5432/convox?sslmode=disable

db-up: ## Start PostgreSQL container
	@docker inspect $(DB_CONTAINER) > /dev/null 2>&1 || \
		docker run -d --name $(DB_CONTAINER) \
			-e POSTGRES_USER=convox \
			-e POSTGRES_PASSWORD=convox \
			-e POSTGRES_DB=convox \
			-p 5432:5432 \
			-v convox-pgdata:/var/lib/postgresql/data \
			postgres:17-alpine
	@docker start $(DB_CONTAINER) 2>/dev/null || true
	@echo "Waiting for PostgreSQL..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		docker exec $(DB_CONTAINER) pg_isready -U convox > /dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo "PostgreSQL running on :5432"
	@$(MAKE) migrate-up

db-down: ## Stop PostgreSQL
	@docker stop $(DB_CONTAINER) 2>/dev/null || true
	@echo "PostgreSQL stopped"

db-reset: ## Destroy and recreate DB (WARNING: deletes data)
	@docker rm -f $(DB_CONTAINER) 2>/dev/null || true
	@docker volume rm convox-pgdata 2>/dev/null || true
	@echo "Database destroyed"
	@$(MAKE) db-up

migrate-up: ## Run all pending migrations
	@dbmate --url "$(DB_URL)" --migrations-dir api/migrations --no-dump-schema up

migrate-down: ## Rollback last migration
	@dbmate --url "$(DB_URL)" --migrations-dir api/migrations --no-dump-schema down

migrate-status: ## Show migration status
	@dbmate --url "$(DB_URL)" --migrations-dir api/migrations status

migrate-new: ## Create new migration (usage: make migrate-new name=add_column)
	@dbmate --url "$(DB_URL)" --migrations-dir api/migrations new $(name)

# --- Backend (Python/FastAPI) -------------------------------------------
api-dev: ## Start API server with hot reload
	@cd api && uv run uvicorn convox.app:create_app --factory \
		--host 0.0.0.0 --port 8000 --reload \
		> /tmp/convox-api.log 2>&1 & echo $$! > ../.pid.api
	@echo "API server running on :8000 (PID: $$(cat .pid.api))"

api-build: ## Type check backend
	@cd api && uv run mypy convox/

api-test: ## Run tests
	@cd api && uv run pytest tests/ -v

# --- Frontend (Vite + React) --------------------------------------------
web-dev: ## Start frontend dev server
	@cd web && bun run dev --port 5173 \
		> /tmp/convox-web.log 2>&1 & echo $$! > ../.pid.web
	@echo "Frontend running on :5173 (PID: $$(cat .pid.web))"

web-build: ## Build frontend for production
	@cd web && bun run build

web-check: ## Type check frontend
	@cd web && bun run tsc --noEmit

# --- Full build ----------------------------------------------------------
build: web-build api-build ## Full production build

# --- Code quality --------------------------------------------------------
fmt: ## Format code
	@cd api && uv run ruff format convox/ tests/

lint: ## Lint code
	@cd api && uv run ruff check convox/ tests/
	@cd api && uv run mypy convox/
	@cd web && bun run tsc --noEmit

clean: ## Remove build artifacts
	@rm -rf web/dist api/__pycache__
	@find api -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
