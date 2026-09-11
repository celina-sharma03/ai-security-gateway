# Phase 0: runs the entry point. Phase 4 swaps CMD for uvicorn.
# The React dashboard builds to static files in a later stage once it exists.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway/ ./gateway/

CMD ["python", "-m", "gateway"]
