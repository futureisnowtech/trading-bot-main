FROM python:3.12-slim

ARG BUILD_SHA=""
LABEL org.opencontainers.image.revision=$BUILD_SHA

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    BUILD_SHA=$BUILD_SHA \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/appuser \
    XDG_CACHE_HOME=/home/appuser/.cache

# Copy dependency files
COPY requirements-runtime.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Copy application code
COPY . .

# Ensure the runtime never writes bind-mounted files as root.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/appuser --shell /bin/sh appuser \
    && mkdir -p /home/appuser/.cache /app/logs \
    && chown -R appuser:appuser /home/appuser /app

# Set environment variable for live confirmation
ENV ALGO_LIVE_CONFIRM="I UNDERSTAND"

USER appuser

# Default to the long-lived lean execution daemon when the image is run directly.
CMD ["python3", "execution_daemon.py"]
