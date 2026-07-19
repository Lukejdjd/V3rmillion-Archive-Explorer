FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    ARCHIVE_DATA_DIR=/app/data \
    MANAGE_IFRAMELY=0 \
    IFRAMELY_BACKEND=http://iframely:8061 \
    WEB_CONCURRENCY=2

WORKDIR /app

RUN useradd --create-home --uid 10001 archive \
    && mkdir -p /app/data \
    && chown archive:archive /app/data

COPY --chown=archive:archive requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=archive:archive app ./app
COPY --chown=archive:archive main.py images_layout.json ./
COPY --chown=archive:archive static ./static
COPY --chown=archive:archive stylesheets ./stylesheets
COPY --chown=archive:archive templates ./templates

USER archive
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["python", "-u", "main.py"]
