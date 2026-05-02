FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

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

# Entrypoint to run the bot in live mode
CMD ["python3", "scripts/boot.py", "--mode", "live", "--confirm-live"]
