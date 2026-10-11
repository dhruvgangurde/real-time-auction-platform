FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /bin/uv

# venv outside /app so the ./backend:/app bind mount does not hide it.
# Downloads are serialised with a long timeout so slow links still finish.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=1800 \
    UV_CONCURRENT_DOWNLOADS=2

WORKDIR /app
COPY pyproject.toml uv.lock ./
# cache mount keeps downloaded wheels across rebuilds and retries
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project

COPY . .
