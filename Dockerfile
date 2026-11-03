FROM python:3.12-slim

WORKDIR /app

COPY requirements-websocket.txt ./

RUN pip install --no-cache-dir -r requirements-websocket.txt

COPY server.py websocket_service.py config.json ./

RUN mkdir -p /app/database

ENV PYTHONUNBUFFERED=1

EXPOSE 10000

CMD ["python", "websocket_service.py"]
