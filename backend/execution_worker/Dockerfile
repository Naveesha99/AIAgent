FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="ai-data-analyst-execution-worker" \
      org.opencontainers.image.version="0.1.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /worker

RUN groupadd --gid 10001 worker \
    && useradd --uid 10001 --gid worker --home-dir /worker \
        --no-create-home --shell /usr/sbin/nologin worker

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=worker:worker worker ./worker

# Read-only dataset input mount point. The root filesystem is read-only at
# execution time; this directory is never used for worker runtime files.
RUN mkdir -p /input \
    && chown root:root /input \
    && chmod 0555 /input

USER 10001:10001
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "-m", "worker.health"]

ENTRYPOINT ["python", "-m", "worker.main"]
