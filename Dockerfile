# Base Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GATEWAY_CONFIG_PATH=/app/config.yaml

# Set working directory
WORKDIR /app

# Install system dependencies (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY gateway/ /app/gateway/
COPY watcher/ /app/watcher/
COPY config.example.yaml /app/config.example.yaml

# Expose Gateway port
EXPOSE 8000

# Default command: run Llama Gateway
CMD ["python", "-m", "gateway.main"]
