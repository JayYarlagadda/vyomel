# Vyomel API / worker / scheduler image.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VYOMEL_ENV=prod \
    VYOMEL_LOG_FORMAT=json \
    VYOMEL_WORKSPACE_ROOT=/var/vyomel

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY vyomel ./vyomel
COPY config ./config
COPY alembic.ini ./

RUN pip install --upgrade pip \
 && pip install .

RUN useradd --create-home --uid 10001 vyomel \
 && mkdir -p /var/vyomel \
 && chown -R vyomel:vyomel /var/vyomel /app
USER vyomel

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["vyomel"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
