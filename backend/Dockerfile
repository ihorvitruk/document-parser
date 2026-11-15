FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Precompile the SDK once, so containers start fast and without compile warnings.
RUN python -W ignore -m compileall -q -j0 /usr/local/lib/python3.13/site-packages/google > /dev/null

COPY app ./app

# API by default; the worker service overrides the command in docker-compose.yml.
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
