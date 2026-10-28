# syntax=docker/dockerfile:1
# Build from the repository root. Dependency versions come from both lockfiles.
FROM node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS frontend
WORKDIR /app/apps/chefmate-playground/frontend
COPY apps/chefmate-playground/frontend/package.json apps/chefmate-playground/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY apps/chefmate-playground/frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753 AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY packages/ packages/
COPY apps/chefmate-playground/pyproject.toml apps/chefmate-playground/uv.lock apps/chefmate-playground/README.md apps/chefmate-playground/
COPY apps/chefmate-playground/src/ apps/chefmate-playground/src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --project apps/chefmate-playground --frozen --no-dev

FROM python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/apps/chefmate-playground/.venv/bin:$PATH \
    CHEFMATE_PLAYGROUND_HOST=0.0.0.0 \
    CHEFMATE_PLAYGROUND_PORT=18765 \
    CHEFMATE_RUNTIME_DIR=/var/lib/chefmate \
    CHEFMATE_USDA_DATABASE=""
WORKDIR /app
RUN groupadd --gid 10001 chefmate && useradd --uid 10001 --gid chefmate --no-create-home chefmate \
    && mkdir -p /var/lib/chefmate /var/lib/chefmate-catalog-status \
    && chown chefmate:chefmate /var/lib/chefmate /var/lib/chefmate-catalog-status
COPY --from=python-build /app/packages/ packages/
COPY --from=python-build /app/apps/chefmate-playground/ apps/chefmate-playground/
COPY apps/chefmate-playground/releases.json apps/chefmate-playground/
COPY apps/chefmate-playground/skills/ apps/chefmate-playground/skills/
COPY docker/entrypoint.py docker/entrypoint.py
COPY docker/seed_catalog.py docker/healthcheck.py docker/
COPY data/seed/catalog/ data/seed/catalog/
COPY --from=frontend /app/apps/chefmate-playground/web/ apps/chefmate-playground/web/
ARG CHEFMATE_BUILD_COMMIT=""
ENV CHEFMATE_BUILD_COMMIT=$CHEFMATE_BUILD_COMMIT
LABEL org.opencontainers.image.revision=$CHEFMATE_BUILD_COMMIT
USER chefmate
EXPOSE 18765
VOLUME ["/var/lib/chefmate"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
CMD ["chefmate-playground"]
