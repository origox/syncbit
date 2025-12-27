# Build stage: Install dependencies
FROM python:3.11-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev

WORKDIR /build

# Copy dependency files
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# Runtime stage: Minimal production image
FROM python:3.11-alpine

# Install runtime dependencies only
RUN apk add --no-cache \
    libffi \
    ca-certificates

# Create non-root user
RUN addgroup -g 1000 syncbit && \
    adduser -D -u 1000 -G syncbit syncbit

# Set up directories
WORKDIR /app

# Create data directory with proper permissions
RUN mkdir -p /app/data && \
    chown -R syncbit:syncbit /app/data

# Create secrets mount point (ESO will mount secrets here in K8s)
RUN mkdir -p /run/secrets && \
    chown -R syncbit:syncbit /run/secrets

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=syncbit:syncbit src/ /app/src/
COPY --chown=syncbit:syncbit main.py /app/
COPY --chown=syncbit:syncbit pyproject.toml /app/

# Switch to non-root user
USER syncbit

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default data directory (can be overridden)
ENV DATA_DIR=/app/data

# Health check - verify imports work
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from src.config import Config; Config.validate()" || exit 1

# Run the application
ENTRYPOINT ["python", "main.py"]
CMD []
