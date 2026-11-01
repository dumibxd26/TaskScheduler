FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY tasks/__init__.py ./tasks/__init__.py
COPY tasks/emulation.py ./tasks/emulation.py
COPY tasks/task_mem/task_mem.py ./
CMD ["python3", "task_mem.py"]
