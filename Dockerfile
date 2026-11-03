FROM python:3.12-slim
WORKDIR /app
COPY requirements-websocket.txt ./
RUN pip install --no-cache-dir -r requirements-websocket.txt
COPY server ./server
RUN mkdir -p /app/database
ENV PYTHONUNBUFFERED=1
EXPOSE 10000
CMD ["python", "server/websocket_service.py"]
