# Stage 1: Frontend build
FROM oven/bun:1 AS frontend
WORKDIR /app/web
COPY web/package.json web/bun.lock* ./
RUN bun install --frozen-lockfile
COPY web/ .
RUN bun run build

# Stage 2: Python backend deps
FROM python:3.12-slim AS backend
WORKDIR /app
RUN pip install uv
COPY api/pyproject.toml api/uv.lock* ./api/
RUN cd api && uv sync --frozen --no-dev
COPY api/ ./api/

# Stage 3: Runtime
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y curl ca-certificates && \
    curl -fsSL -o /usr/local/bin/dbmate \
    https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 && \
    chmod +x /usr/local/bin/dbmate && \
    pip install uv && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=backend /app/api /app/api
COPY --from=frontend /app/web/dist /app/web/dist
COPY api/migrations /app/migrations
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
