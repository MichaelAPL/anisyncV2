# AniSync production image: the API, the in-process job worker and the built frontend, served from one origin.
# Deployed by Kamal (config/deploy.yml). The pre-deploy hook runs one-off commands in this same image
# (alembic, procrastinate, python -m anisync.seed), so keep CMD overridable: no ENTRYPOINT.

# ---- frontend build ------------------------------------------------------------------------------------------------
FROM node:24-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend -------------------------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend
# Dependencies first, so code changes don't reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --locked --no-dev

COPY --from=frontend /web/dist /app/frontend/dist

RUN useradd --system --uid 10001 --no-create-home anisync
USER anisync

ENV PATH=/app/.venv/bin:$PATH \
    FRONTEND_DIST=/app/frontend/dist

EXPOSE 8000
# uvicorn trusts forwarded headers only from FORWARDED_ALLOW_IPS (set in config/deploy.yml to the private Docker
# network); its default trusts only localhost.
CMD ["uvicorn", "anisync.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
