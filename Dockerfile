# One image, two roles: back office (`serve`) and orchestrator (one container per mosque live).
FROM python:3.12-slim-bookworm

# ffmpeg (Debian build: zmq, srt, rtmp) + the libraries chrome-headless-shell needs
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates fonts-dejavu-core \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY sada sada
COPY assets assets
COPY docs docs
COPY web/dist web/dist
RUN uv sync --frozen --no-dev \
    && python -c "from sada.backoffice import template_render as t; t.fetch_engine()"

ARG SADA_VERSION=dev
ENV SADA_VERSION=$SADA_VERSION

# Non-root runtime user. /app stays root-owned (read-only for the app: code, venv, rendering engine);
# only data/ (database, recordings, outputs) and the home (model caches) are writable.
# The image ships no CUDA libraries: transcription runs on CPU (or remote) in containers; GPU is local-only.
# Existing volumes created by a root container must be chown'ed to 10001 once (or use fsGroup: 10001).
RUN useradd --system --uid 10001 --user-group --create-home --home-dir /home/sada sada \
    && mkdir -p /app/data && chown sada:sada /app/data
USER sada

# data/ (database, recordings, outputs) lives on a volume
VOLUME /app/data
EXPOSE 8090 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "sada.backoffice", "serve", "--host", "0.0.0.0", "--port", "8090"]
