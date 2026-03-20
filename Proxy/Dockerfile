FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

ENV OPENSEARCH_ENDPOINT=""
ENV AWS_REGION="us-east-1"
ENV PROXY_LISTEN="0.0.0.0:9200"

EXPOSE 9200

CMD ["gunicorn", "-b", "0.0.0.0:9200", "-w", "4", "--threads", "2", "app:app"]
