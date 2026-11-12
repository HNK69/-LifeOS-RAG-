# syntax=docker/dockerfile:1
# LifeOS Production Containerfile
# Base: Official Python 3.12 slim-bookworm for minimal attack surface
FROM python:3.12-slim-bookworm

# System dependencies for PyTorch / C extensions if required
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Ensure unbuffered logs and bytecode compilation suppression
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/app

# Create unprivileged application user
RUN groupadd -g 10001 lifeos && \
    useradd -u 10001 -g lifeos -s /bin/bash -m lifeos

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY --chown=lifeos:lifeos app/ /app/app/
COPY --chown=lifeos:lifeos .env.example /app/.env.example

# Create writable persistent state directories
RUN mkdir -p /app/data/documents /app/data/backups /app/chroma_db && \
    chown -R lifeos:lifeos /app/data /app/chroma_db

# Switch to unprivileged user
USER lifeos:lifeos

# Volumes for persistent data across container recreation
VOLUME ["/app/data", "/app/chroma_db"]

# Expose HTTP port
EXPOSE 8000

# Container liveness health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# Production ASGI server entrypoint (Single-process architecture)
ENTRYPOINT ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
