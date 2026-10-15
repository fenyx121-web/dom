FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY storybot ./storybot
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /work
CMD ["python", "-m", "storybot", "telegram"]
