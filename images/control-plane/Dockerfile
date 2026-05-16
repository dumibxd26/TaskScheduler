# Shared base image for both ts-scheduler and ts-controller. Each deployment
# overrides the command via its own manifest, so we don't bake one in.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the control-plane needs to keep the image small.
COPY models/    ./models/
COPY services/  ./services/
COPY cmd/       ./cmd/
COPY engine.py  ./engine.py

# Default command is the scheduler; the controller deployment overrides it.
CMD ["python3", "-m", "cmd.scheduler.main"]
