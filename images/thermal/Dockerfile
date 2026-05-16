FROM python:3.11-alpine

RUN pip install --no-cache-dir kubernetes==29.0.0

WORKDIR /app
COPY collect.py /app/collect.py

ENTRYPOINT ["python3", "/app/collect.py"]
