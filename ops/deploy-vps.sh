#!/usr/bin/env bash
# One-command test deployment on a fresh Ubuntu 22.04/24.04 VPS (2+ vCPU, 4+ GB RAM, 20+ GB disk).
# Usage (as root):  bash deploy-vps.sh [domain]
#   with a domain pointing to the server -> HTTPS via Caddy (Let's Encrypt); without -> http://<server-ip>
set -euo pipefail
DOMAIN="${1:-}"
APP_DIR=/opt/nap-store
if ! command -v docker >/dev/null; then
  apt-get update -y && apt-get install -y ca-certificates curl unzip
  curl -fsSL https://get.docker.com | sh
fi
mkdir -p "$APP_DIR"
if [ -f ./nap-store.zip ]; then unzip -oq ./nap-store.zip -d /opt && true; fi
cd "$APP_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^NAP_SECRET_KEY=.*/NAP_SECRET_KEY=$(head -c 32 /dev/urandom | base64 | tr -dc A-Za-z0-9)/" .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -dc A-Za-z0-9)/" .env
  sed -i "s/^MINIO_ROOT_PASSWORD=.*/MINIO_ROOT_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -dc A-Za-z0-9)/" .env
fi
IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
if [ -n "$DOMAIN" ]; then SITE="$DOMAIN"; URL="https://$DOMAIN"; SECURE=true; else SITE=":80"; URL="http://$IP"; SECURE=false; fi
sed -i "s#^NAP_PUBLIC_BASE_URL=.*#NAP_PUBLIC_BASE_URL=$URL#" .env
grep -q '^NAP_COOKIE_SECURE=' .env && sed -i "s/^NAP_COOKIE_SECURE=.*/NAP_COOKIE_SECURE=$SECURE/" .env || echo "NAP_COOKIE_SECURE=$SECURE" >> .env
cat > Caddyfile <<CADDY
$SITE {
  encode gzip
  reverse_proxy web:3000
}
CADDY
cat > docker-compose.override.yml <<'OVR'
services:
  api:
    ports: !reset []
    environment:
      NAP_COOKIE_SECURE: ${NAP_COOKIE_SECURE:-false}
  web:
    ports: !reset []
  caddy:
    image: caddy:2.10
    ports: ["80:80", "443:443"]
    volumes: [./Caddyfile:/etc/caddy/Caddyfile:ro, caddy_data:/data]
    depends_on: [web]
    networks: [edge]
volumes: { caddy_data: {} }
OVR
docker compose up -d --build
echo "Waiting for the demo data (real conversions)…"
for i in $(seq 1 90); do
  if docker compose ps migrate --format '{{.State}}' 2>/dev/null | grep -q exited; then break; fi; sleep 5
done
docker compose logs migrate | tail -3
echo "NAP Store is up: $URL   (demo accounts in README, password demo-password-1)"
