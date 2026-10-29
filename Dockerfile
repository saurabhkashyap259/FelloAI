FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
RUN uv pip install --system --no-cache . && useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn worklist.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
