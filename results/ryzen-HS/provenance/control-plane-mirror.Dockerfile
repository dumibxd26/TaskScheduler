FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://packagefeedproxy.microsoft.io/pypi/simple/ -r requirements.txt
COPY models/ ./models/
COPY services/ ./services/
COPY controlplane/ ./controlplane/
COPY engine.py ./engine.py
CMD ["python3", "-m", "controlplane.scheduler.main"]
