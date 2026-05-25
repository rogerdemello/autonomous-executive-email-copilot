FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:7860/health', timeout=5).raise_for_status()"

# Startup wait for services that need time to initialize
ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "sleep 2 && uvicorn env.api:app --host 0.0.0.0 --port 7860"]