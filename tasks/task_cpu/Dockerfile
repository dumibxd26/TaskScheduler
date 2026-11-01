FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY tasks/__init__.py ./tasks/__init__.py
COPY tasks/emulation.py ./tasks/emulation.py
COPY tasks/task_cpu/task_cpu.py ./
CMD ["python3", "task_cpu.py"]
