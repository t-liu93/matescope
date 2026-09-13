FROM node:22.23.1-bookworm-slim AS frontend-build
WORKDIR /src/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.npmrc ./
RUN pnpm install --frozen-lockfile
RUN pnpm rebuild esbuild --config.ignore-scripts=false
COPY frontend/ ./
RUN pnpm build

FROM python:3.13.8-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
RUN groupadd --system matescope && useradd --system --gid matescope --home-dir /app --shell /usr/sbin/nologin matescope
COPY --from=frontend-build /src/frontend/dist /app/frontend/dist
COPY backend /app/backend
COPY pyproject.toml uv.lock /app/
RUN pip install --no-cache-dir uv==0.11.8 && uv sync --locked --no-dev
RUN mkdir -p /app/data && chown -R matescope:matescope /app
USER matescope
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
CMD ["/app/.venv/bin/uvicorn", "matescope.main:app", "--host", "0.0.0.0", "--port", "8000"]
