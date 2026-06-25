# ── Build stage ───────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────
FROM python:3.11-slim

# Non-root user for security
RUN groupadd -r hirepilot && useradd -r -g hirepilot -d /app -s /sbin/nologin hirepilot

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application source (exclude dev/test files via .dockerignore)
COPY --chown=hirepilot:hirepilot . .

# Persistent data volume: SQLite DB lives here
RUN mkdir -p /data && chown hirepilot:hirepilot /data
VOLUME ["/data"]

# Streamlit port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

USER hirepilot

# HIREPILOT_SECRET_KEY and HIREPILOT_DB_PATH must be set via env/secret
ENV HIREPILOT_DB_PATH=/data/hirepilot.db

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=true"]
