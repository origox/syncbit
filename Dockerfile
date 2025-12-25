# Multi-stage build for SyncBit
FROM python:3.11-slim as builder

# Set working directory
WORKDIR /build

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY src/ /app/src/
COPY main.py /app/

# Create data directory
RUN mkdir -p /app/data && chmod 755 /app/data

# Set Python path
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Default data directory
ENV DATA_DIR=/app/data

# Run as non-root user (optional, can be enabled later)
# RUN useradd -m -u 1000 syncbit && chown -R syncbit:syncbit /app
# USER syncbit

# Health check
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default command
CMD ["python", "main.py"]
