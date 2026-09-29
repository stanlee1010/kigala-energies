FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p uploads reports

EXPOSE 8000
# One process runs the background monitor; threads handle concurrent requests.
CMD gunicorn --workers 1 --threads 8 --bind 0.0.0.0:${PORT} "backend.app:create_app()"
