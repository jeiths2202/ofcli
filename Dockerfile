FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/
COPY api/ api/
COPY cli/ cli/
COPY .env .env

# Expose API port
EXPOSE 8000

# Run API server
CMD ["sh", "-c", "python -m uvicorn api.server:app --host 0.0.0.0 --port ${API_PORT:-8000} --log-level info"]
