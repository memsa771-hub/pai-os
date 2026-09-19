# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Keep downloads between interrupted/repeated BuildKit builds. The installed
# packages still live in the image; only pip's wheel/download cache is mounted.
COPY requirements.txt .
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --prefer-binary -r requirements.txt

# Production uses Browser Fabric (cloud) via BROWSERFABRIC_API_KEY — all
# browser operations are proxied via REST API, no local Chromium needed. This
# image still installs it: without BROWSERFABRIC_API_KEY set (self-hosted /
# local dev), app/browser.py falls back to launching Chromium in-process, and
# a missing binary there is a 500 with no way to configure around it short of
# getting a Browser Fabric key.
# Cache Chromium's download separately, then copy the installed browser into
# the image. A cache mount by itself is not part of the final image.
RUN playwright install-deps chromium
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN --mount=type=cache,target=/root/.cache/playwright,sharing=locked \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/playwright \
    playwright install chromium \
    && mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" \
    && cp -a /root/.cache/playwright/. "$PLAYWRIGHT_BROWSERS_PATH"/

# Copy application code
COPY . .

RUN chmod +x entrypoint.sh

ENV PYTHONPATH=/app

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
