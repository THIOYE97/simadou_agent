FROM python:3.12-slim

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY sanctions_agent/ ./sanctions_agent/

# Non-root user for security
RUN useradd -m agent
USER agent

# Default: run scheduler (overridable with --run-now for one-shot)
CMD ["python", "-m", "sanctions_agent.main"]
