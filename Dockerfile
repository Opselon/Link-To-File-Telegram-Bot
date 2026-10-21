# syntax: docker/dockerfile:1
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# ffmpeg is the only system dependency (media muxing + mp3). Alpine's package
# is ~10x smaller than Debian's, which keeps the image around 200 MB.
RUN apk add --no-cache ffmpeg ca-certificates tzdata \
    && rm -rf /var/cache/apk/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true

COPY . .

RUN adduser -D -u 10001 -h /home/bot bot \
    && mkdir -p /data \
    && chown -R bot:bot /data /app

USER bot
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

ENTRYPOINT ["python", "main.py"]
