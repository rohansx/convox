#!/bin/sh
set -e
echo "Running migrations..."
dbmate --url "$DATABASE_URL" --migrations-dir ./migrations --no-dump-schema up
echo "Starting Convox..."
cd api && uv run uvicorn convox.app:create_app --factory --host 0.0.0.0 --port 8000
