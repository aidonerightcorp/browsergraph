# browsergraph — one image, two modes:
#   docker run ... doctor            (CLI)
#   docker run -p 8800:8800 ...      (HTTP service, the CMD default)
#
# Build args pick which engines are baked in. The core is stdlib-only, so the
# base image stays small when you only need mock/CDP.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS base

ARG EXTRAS=playwright
ARG INSTALL_BROWSERS=chromium

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8800 \
    BG_ENGINE=playwright \
    BG_DISPLAY=headless \
    OLLAMA_HOST=http://host.docker.internal:11434 \
    OLLAMA_MODEL=glm-5.2

# Playwright/Selenium browser runtime deps. Skipped cost is not worth the
# complexity of a second Dockerfile; strip this layer for a mock-only image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY browsergraph ./browsergraph
RUN pip install ".[${EXTRAS}]"

# Download browser binaries for the chosen engine family (no-op if not needed).
RUN if [ -n "${INSTALL_BROWSERS}" ] && python -c "import playwright" 2>/dev/null; then \
        playwright install --with-deps ${INSTALL_BROWSERS}; \
    fi

RUN useradd -m -u 10001 bg && chown -R bg:bg /app
USER bg

EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')" || exit 1

ENTRYPOINT ["browsergraph"]
CMD ["serve"]
