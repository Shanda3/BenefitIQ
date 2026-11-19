# Cloudflare Containers run linux/amd64. The local embedding model is baked in
# so the container does not download it on boot. Query vectors must match the
# vectors stored by `make load-pilot`, which uses the same model.
FROM --platform=linux/amd64 python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    HF_HOME=/opt/fastembed

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /opt/fastembed \
    && chown app:app /opt/fastembed

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed')" \
    && chown -R app:app /opt/fastembed

COPY . .
RUN chmod 755 /app/docker/entrypoint.sh && chown -R app:app /app

USER app
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["serve"]
