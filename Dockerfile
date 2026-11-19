FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["sh", "-c", "python -m benefitiq.cli wait-db && python -m benefitiq.cli init-db && python -m benefitiq.cli ingest --manifest corpus/stub/manifest.yaml && python -m benefitiq.cli ingest --manifest corpus/office/manifest.yaml && python -m benefitiq.cli ingest --manifest corpus/payers/manifest.yaml && uvicorn benefitiq.main:app --host 0.0.0.0 --port 8000"]
