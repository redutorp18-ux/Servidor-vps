FROM python:3.12-slim
WORKDIR /app
COPY requirements-websocket.txt ./
RUN pip install --no-cache-dir -r requirements-websocket.txt
COPY server ./server
RUN mkdir -p /app/database
ENV PYTHONUNBUFFERED=1
ENV FF170_BUILD_VERSION=phase-online-v2
EXPOSE 10000
CMD ["python", "server/websocket_service.py"]
