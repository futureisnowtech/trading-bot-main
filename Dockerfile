FROM python:3.12-slim

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# Copy dependency files
COPY requirements-runtime.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Copy application code
COPY . .

# Ensure logs directory exists
RUN mkdir -p logs

# Set environment variable for live confirmation
ENV ALGO_LIVE_CONFIRM="I UNDERSTAND"

# Default to a valid single-pass execution entrypoint when the image is run directly.
CMD ["python3", "sniper_cron.py"]
