FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "durable_reminders.main:app", "--host", "0.0.0.0", "--port", "8000"]
