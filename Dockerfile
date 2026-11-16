FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# requirements.lock pins every package (and its dependencies) to an exact version with hashes, so two builds of the same
# commit contain the same code and a tampered or swapped package fails the install. Refresh it with scripts/update_lock.sh.
COPY requirements.lock .
RUN pip install --require-hashes --no-deps -r requirements.lock

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN useradd --create-home --uid 10001 opspulse
USER opspulse

EXPOSE 8000
# Liveness only; readiness (database) is GET /readyz.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

# Apply migrations, then serve. A failed migration stops the container instead of serving a stale schema.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn src.main:app --host 0.0.0.0 --port 8000"]
