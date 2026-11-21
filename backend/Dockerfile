# Multi-stage Dockerfile for Cloud Run using uv
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml /app/
RUN uv sync --no-install-project --no-dev

# Copy application source code
COPY app/ /app/app/
COPY README.md /app/README.md

# Install application package
RUN uv sync --no-dev

# Final runtime image
FROM python:3.12-slim-bookworm AS runtime

# Set security and Cloud Run environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

WORKDIR /app

# Copy virtual environment and app code from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app

# Non-root user for Cloud Run security best practices
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Cloud Run dynamic port binding
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
