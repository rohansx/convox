# Claude Code Instructions

## Commit Messages
- Single-line commit messages only
- Do NOT include `Co-Authored-By` lines
- Keep messages concise and descriptive

## Project
- FastAPI backend in `api/convox/`
- Env file at repo root `.env`
- Lint with `uv run ruff check` from `api/`
- Run server: source `.env` then `uv run uvicorn convox.app:create_app --factory --port 8000`
