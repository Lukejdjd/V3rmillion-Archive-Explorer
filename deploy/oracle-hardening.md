# Oracle VM hardening checklist (Always Free Ampere A1)
#
# Run these on the Ubuntu VM after first SSH login. Do not expose API/Iframely
# ports publicly — only 22 (your IP), 80, and 443.

## 1. SSH keys only
# Disable password auth after confirming key login works:
#   sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
#   sudo systemctl reload ssh

## 2. Firewall (UFW)
#   sudo apt-get update && sudo apt-get install -y ufw
#   sudo ufw default deny incoming
#   sudo ufw default allow outgoing
#   sudo ufw allow OpenSSH
#   sudo ufw allow 80/tcp
#   sudo ufw allow 443/tcp
#   sudo ufw enable
#   sudo ufw status verbose

## 3. Keep the OS updated
#   sudo apt-get update && sudo apt-get upgrade -y
#   sudo apt-get autoremove -y

## 4. Docker / service permissions
# Run compose as a non-root user in the docker group.
# Archive container already runs as uid 10001 with a read-only rootfs.

## 5. Protect secrets
# Keep TURNSTILE_SECRET_KEY and other secrets in `.env` (never commit).
#   chmod 600 .env

## 6. Backups
# Keep at least:
# - current + previous GitHub Release .zst databases + SHA256SUMS
# - `.env` and `deploy/Caddyfile` copies offline
# - `static/` / Pages `dist/` if customized locally
#
# Example offline backup:
#   mkdir -p ~/archive-backups/$(date +%F)
#   cp .env deploy/Caddyfile ~/archive-backups/$(date +%F)/
#   cp data/downloads/*.zst data/downloads/SHA256SUMS ~/archive-backups/$(date +%F)/

## 7. Monitoring alerts (Uptime Kuma)
# Create monitors for:
# - https://api.example.com/healthz
# - https://your-pages-host/static/search.html
# Alert on: high CPU (Netdata), low disk, origin offline.
