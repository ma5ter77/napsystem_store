#!/usr/bin/env bash
# NAP-087: one-time (re-runnable) setup of a fresh Ubuntu 22.04/24.04 server for store.napsystem.com.
# Build the self-contained installer with `python3 ops/vps/make-installer.py` and run it as root:
#   ssh root@SERVER_IP 'bash -s' < nap-vps-install.sh
# What it does: system updates + automatic security updates, firewall (22/80/443), fail2ban, swap,
# Docker from the Ubuntu repository, the unprivileged `napdeploy` user, the release runner in
# /opt/nap-deploy (root-owned), /srv/nap-store, a shared/.env with freshly generated secrets that never
# leaves the server, and a GitHub Actions deploy key restricted to the release runner.
# Re-running keeps the existing .env, data and deploy key (pass NEW_DEPLOY_KEY=1 to replace the key).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DOMAIN="${STORE_DOMAIN:-store.napsystem.com}"
ROOT=/srv/nap-store
OPT=/opt/nap-deploy
USER_NAME=napdeploy

[ "$(id -u)" = 0 ] || { echo "Run as root"; exit 1; }
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) echo "Ubuntu/Debian only (found $ID)"; exit 1;; esac
say() { printf '\n== %s\n' "$*"; }

say "System packages and automatic security updates"
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confold upgrade
apt-get -y -q install ca-certificates curl openssl ufw fail2ban unattended-upgrades python3 openssh-server
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true

say "Docker (Ubuntu repository)"
if ! command -v docker >/dev/null; then
  apt-get -y -q install docker.io
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get -y -q install docker-compose-v2 || apt-get -y -q install docker-compose-plugin
fi
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
  # mirror.gcr.io: Docker Hub mirror, used when Docker Hub is slow or unreachable from the hosting region
  cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "registry-mirrors": ["https://mirror.gcr.io"],
  "live-restore": true
}
JSON
fi
systemctl enable docker >/dev/null
systemctl restart docker
docker compose version

say "Swap (2 GiB) if none"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q vm.swappiness=10; echo 'vm.swappiness=10' > /etc/sysctl.d/90-nap-swap.conf
fi

say "Firewall: SSH, HTTP, HTTPS"
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null
ufw status | head -12

say "Release user and directories"
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
usermod -aG docker "$USER_NAME"
passwd -l "$USER_NAME" >/dev/null
install -d -m 755 -o root -g root "$OPT"
install -d -m 750 -o "$USER_NAME" -g "$USER_NAME" "$ROOT" "$ROOT/releases" "$ROOT/state" "$ROOT/backups"
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$ROOT/shared"

say "Release runner"
printf '%s' "__NAPDEPLOY_B64__" | base64 -d > "$OPT/napdeploy.py.new"
python3 -m py_compile "$OPT/napdeploy.py.new"
install -m 755 -o root -g root "$OPT/napdeploy.py.new" "$OPT/napdeploy.py"; rm -f "$OPT/napdeploy.py.new"
rm -rf "$OPT/__pycache__"
cat > "$OPT/config.json" <<JSON
{"root": "$ROOT", "registry": "ghcr.io", "image_prefix": "ghcr.io/ma5ter77/nap-store-", "keep": 5, "ready_seconds": 180}
JSON
chmod 644 "$OPT/config.json"

say "Application settings (secrets are generated here and stay on this server)"
ENV="$ROOT/shared/.env"
if [ ! -f "$ENV" ]; then
  umask 077
  cat > "$ENV" <<EOF
# NAP Store settings. Edit on the server only: sudo -u $USER_NAME nano $ENV
# Apply a change: GitHub -> Actions -> store-ops -> Run workflow -> restart.
NAP_ENV=demo
NAP_SEED_DEMO=1
STORE_DOMAIN=$DOMAIN
POSTGRES_PASSWORD=$(openssl rand -hex 24)
NAP_SECRET_KEY=$(openssl rand -hex 32)
NAP_API_WORKERS=2
NAP_PAYMENT_PROVIDER=mock
NAP_SHIPPING_ADAPTER=mock
NAP_EMAIL_BACKEND=log
BACKUP_AT=23:30
BACKUP_KEEP_DAYS=14
EOF
  chown "$USER_NAME:$USER_NAME" "$ENV"; chmod 600 "$ENV"
  echo "created $ENV (values not shown)"
else
  echo "kept existing $ENV"
fi

say "GitHub Actions deploy key (can only run the release runner)"
AK="/home/$USER_NAME/.ssh/authorized_keys"
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
PRIV=""
if [ ! -s "$AK" ] || [ "${NEW_DEPLOY_KEY:-0}" = 1 ]; then
  T=$(mktemp -d); ssh-keygen -q -t ed25519 -N '' -C github-actions-nap-store-deploy -f "$T/k"
  echo "command=\"/usr/bin/python3 $OPT/napdeploy.py gate\",restrict $(cat "$T/k.pub")" > "$AK"
  PRIV=$(cat "$T/k"); rm -rf "$T"
  chown "$USER_NAME:$USER_NAME" "$AK"; chmod 600 "$AK"
fi

say "SSH hardening"
if [ -s /root/.ssh/authorized_keys ]; then
  cat > /etc/ssh/sshd_config.d/90-nap.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  sshd -t && (systemctl reload ssh 2>/dev/null || systemctl reload sshd)
  echo "password login disabled (root keeps key login)"
else
  echo "WARNING: root has no SSH key yet, password login left ON. Add your key (ssh-copy-id) and re-run."
fi

IP=$(curl -4 -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
HOSTKEY=$(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)
cat <<EOF

================================================================================
 READY. Put these into GitHub: github.com/ma5ter77/napsystem_store -> Settings
   -> Secrets and variables -> Actions
--------------------------------------------------------------------------------
 Variables tab (not secret, may also be sent to Claude):
   STORE_DEPLOY_HOST      $IP
   STORE_DEPLOY_HOST_KEY  $HOSTKEY
EOF
if [ -n "$PRIV" ]; then
cat <<EOF
 Secrets tab -> New repository secret  (do NOT send it to anyone, chat or Claude):
   Name:  STORE_DEPLOY_SSH_KEY
   Value: everything between the lines below, including BEGIN/END
-----8<-------------------------------------------------------------------------
$PRIV
-----8<-------------------------------------------------------------------------
 The key is not stored on this server; if lost, re-run with NEW_DEPLOY_KEY=1.
EOF
else
  echo " Deploy key already installed (secret STORE_DEPLOY_SSH_KEY unchanged)."
fi
cat <<EOF
 DNS (REG.RU): A record  store  ->  $IP
================================================================================
EOF
