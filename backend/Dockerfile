FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY pyproject.toml ./


# Test image: `docker compose --profile test run --rm tests`
FROM base AS test
RUN uv pip install --system --no-cache -r pyproject.toml --extra dev
COPY app ./app
COPY tests ./tests
CMD ["python", "-m", "pytest", "--cov=app", "--cov-report=term-missing:skip-covered"]


# Runtime image (the default, because it is the last stage): API by default; the
# worker service overrides the command in docker-compose.yml.
FROM base AS runtime
RUN uv pip install --system --no-cache -r pyproject.toml

# Precompile the SDK once, so containers start fast and without compile warnings.
RUN python -W ignore -m compileall -q -j0 /usr/local/lib/python3.13/site-packages/google > /dev/null

COPY app ./app

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
