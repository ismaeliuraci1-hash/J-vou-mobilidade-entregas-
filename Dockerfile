FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
ENV JAVOU_HOST=0.0.0.0
EXPOSE 10000
CMD ["python3", "server.py"]
