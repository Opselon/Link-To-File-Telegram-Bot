#!/usr/bin/env bash
# scripts/remote-deploy.sh — Turnkey Continuous Deployment runner for Link-To-File Bot
#
# Designed to run remotely via GitHub Actions (appleboy/ssh-action) or directly
# on any Linux host. Ingests GitHub Secrets passed as environment variables,
# writes/syncs .env securely, pulls the latest GHCR image, cleanly tears down
# previous container, starts the fresh stack, and verifies container health.
#
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/root/link-to-file}"
COMPOSE_PROJECT="link-to-file"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/opselon/link-to-file-telegram-bot}"
FULL_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"

echo "============================================================"
echo "  Link-To-File Bot — Continuous Deployment Runner"
echo "  Target directory: $DEPLOY_DIR"
echo "  Target image:     $FULL_IMAGE"
echo "============================================================"

# 1. Prepare deploy directory and ensure docker-compose.yml exists
mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

if [ ! -f "$DEPLOY_DIR/docker-compose.yml" ]; then
  echo "== creating default docker-compose.yml at $DEPLOY_DIR =="
  cat > "$DEPLOY_DIR/docker-compose.yml" << 'COMPOSE_EOF'
name: link-to-file

services:
  bot:
    image: ${BOT_IMAGE:-ghcr.io/opselon/link-to-file-telegram-bot:latest}
    container_name: link-to-file-bot
    restart: unless-stopped
    env_file: .env
    environment:
      DATA_DIR: /data
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    volumes:
      - bot-data:/data
    extra_hosts:
      - host.docker.internal:host-gateway
    stop_grace_period: 30s
    healthcheck:
      test: ["CMD", "python", "scripts/healthcheck.py"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 40s
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  bot-data:
COMPOSE_EOF
fi

# 2. Synchronize environment variables and secrets into .env safely
ENV_FILE="$DEPLOY_DIR/.env"
TMP_ENV="$(mktemp "$DEPLOY_DIR/.env.tmp.XXXXXX")"
trap 'rm -f "$TMP_ENV"' EXIT

if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$TMP_ENV"
else
  touch "$TMP_ENV"
fi

SECRET_KEYS=(
  "BOT_TOKEN"
  "ADMIN_ID"
  "MAX_FILE_SIZE_MB"
  "DOWNLOAD_TIMEOUT"
  "QUEUE_CONCURRENCY"
  "RATE_LIMIT_SECONDS"
  "LOG_LEVEL"
  "BOT_DISPLAY_NAME"
  "YTDLP_PROXY"
  "INSTANT_PROXY"
  "TELEGRAM_API_URL"
  "BOT_IMAGE"
)

upsert_env_key() {
  local key="$1"
  local val="$2"
  if grep -q "^${key}=" "$TMP_ENV"; then
    python3 -c "
import sys
key = sys.argv[1]
val = sys.argv[2]
path = sys.argv[3]
with open(path, 'r') as f:
    lines = f.readlines()
with open(path, 'w') as f:
    for line in lines:
        if line.startswith(key + '='):
            f.write(f'{key}={val}\n')
        else:
            f.write(line)
" "$key" "$val" "$TMP_ENV"
  else
    echo "${key}=${val}" >> "$TMP_ENV"
  fi
}

for key in "${SECRET_KEYS[@]}"; do
  val="${!key:-}"
  if [ -n "$val" ]; then
    upsert_env_key "$key" "$val"
  fi
done

upsert_env_key "BOT_IMAGE" "$FULL_IMAGE"

mv "$TMP_ENV" "$ENV_FILE"
chmod 0600 "$ENV_FILE"
echo "== synced environment secrets to .env (chmod 0600) =="

# 3. Pull target Docker image
echo "== pulling fresh Docker image '$FULL_IMAGE' =="
docker pull "$FULL_IMAGE"

# 4. Clean container teardown ("همیشه داکر قبلی رو بخوابونه جدید و تمیز بیاد")
echo "== cleanly stopping and removing previous container =="
docker stop -t 15 link-to-file-bot 2>/dev/null || true
docker rm -f link-to-file-bot 2>/dev/null || true

# 5. Start fresh container
echo "== launching clean container =="
docker compose -p "$COMPOSE_PROJECT" --env-file "$ENV_FILE" up -d --remove-orphans

# 6. Verify container health
echo "== verifying container health (timeout: ${HEALTH_TIMEOUT}s) =="
deadline=$((SECONDS + HEALTH_TIMEOUT))
healthy=0

while [ "$SECONDS" -lt "$deadline" ]; do
  status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' link-to-file-bot 2>/dev/null || echo missing)
  if [ "$status" = "healthy" ]; then
    echo "== container is healthy! =="
    healthy=1
    break
  fi
  sleep 3
done

if [ "$healthy" -ne 1 ]; then
  echo "error: container failed to report healthy within ${HEALTH_TIMEOUT}s (status: $status)" >&2
  docker logs --tail=25 link-to-file-bot >&2 || true
  exit 1
fi

# 7. Prune dangling Docker images
echo "== pruning dangling docker images =="
docker image prune -f || true

echo "============================================================"
echo "  Continuous Deployment Completed Successfully!"
echo "  Active Container: link-to-file-bot"
echo "  Image: $FULL_IMAGE"
echo "============================================================"
docker compose -p "$COMPOSE_PROJECT" ps
docker logs --tail=10 link-to-file-bot
