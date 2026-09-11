# Backend container for Cloud Run. Not used by the frontend (that deploys to
# Netlify as a static build) or by the ingestion pipeline (that runs locally
# or as a separate scheduled job -- it needs raw Excel files on disk, which
# have no reason to live in this image).
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first so this layer is cached across code-only changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY ingestion/ ingestion/
COPY backend/ backend/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8080
# Shell form (not exec form) so ${PORT} actually expands -- Cloud Run injects
# PORT at runtime and expects the container to listen on it. `python -m`
# rather than the bare `uvicorn` console script so /app (containing the
# loose ingestion/ and backend/ source, not just what got installed as a
# wheel) is on sys.path.
CMD python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}
