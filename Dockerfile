# Application image: the FastAPI agent plus the MCP client it spawns.
# Built by docker-compose.yml; the simulated helpdesk API is a separate service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The agent reads the dataset but never writes to it.
COPY app/ ./app/
COPY dataset/ ./dataset/
COPY services/ ./services/
COPY ui/ ./ui/
COPY run.py README.md DESIGN.md ./

RUN mkdir -p /app/data
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=4s --start-period=45s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
