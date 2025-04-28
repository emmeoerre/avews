FROM python:3.9-slim

WORKDIR /app

COPY startup.py /app/startup.py
RUN pip install requests websocket-client

CMD ["python", "startup.py"]