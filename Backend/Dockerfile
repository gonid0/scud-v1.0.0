# Multistage build — итоговый image ~70 MB (vs ~250 MB single-stage),
# не содержит build-tooling. PyNaCl и argon2-cffi требуют сборки нативки,
# поэтому в builder ставим build-essential, а runner — slim.

# ----------------------------------------------------------------------------
# Stage 1: builder
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libsodium-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src

# Собираем wheels всех зависимостей: их перенесём в runner, экономя
# build-стек ~150 MB.
RUN pip wheel --no-deps --wheel-dir /wheels -e .

# ----------------------------------------------------------------------------
# Stage 2: app (runtime)
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libsodium23 + libpq5 — runtime SO для PyNaCl и asyncpg.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsodium23 \
        libpq5 \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r scud && useradd -r -g scud -d /app -s /sbin/nologin scud

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir /wheels/*.whl \
        psycopg2-binary asyncpg \
    && rm -rf /wheels

USER scud

# tini нужен для корректной обработки SIGTERM в uvicorn (без него после
# `docker stop` процесс зависает на 10 сек до SIGKILL).
ENTRYPOINT ["/usr/bin/tini", "--"]

# По умолчанию — API. docker-compose переопределяет на alembic / worker.
CMD ["uvicorn", "scud.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)" \
        || exit 1
