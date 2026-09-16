FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Production uses Browser Fabric (cloud) via BROWSERFABRIC_API_KEY — all
# browser operations are proxied via REST API, no local Chromium needed. This
# image still installs it: without BROWSERFABRIC_API_KEY set (self-hosted /
# local dev), app/browser.py falls back to launching Chromium in-process, and
# a missing binary there is a 500 with no way to configure around it short of
# getting a Browser Fabric key.
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

RUN chmod +x entrypoint.sh

ENV PYTHONPATH=/app

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
