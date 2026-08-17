---
name: aegis-deploy
description: Deploy AEGIS to production on Oracle Cloud via SSH (git pull, docker compose build, recreate, verify)
---

# AEGIS V1.3 Production Deployment

Automates the full deployment pipeline for the AEGIS Trading Bot to the production server.

## Server Details

- **Host**: `143.47.112.212`
- **User**: `ubuntu`
- **SSH Key**: `~/.ssh/gcloud_hermes`
- **Project Path**: `/home/ubuntu/aegis`
- **Compose File**: `docker-compose.prod.yml`
- **App Container**: `aegis-api`
- **Local Project**: `C:\Users\rafael.dacosta\source\repos\Aegis`

## Deployment Procedure

### Step 1: Commit and Push (local)

Always commit local changes first. The server pulls from `origin main`.

```bash
git add -A
git commit -m "<message>"
git push origin main
```

### Step 2: Deploy on Server

SSH into the server, pull latest, rebuild, and restart:

```bash
ssh -i ~/.ssh/gcloud_hermes ubuntu@143.47.112.212 "cd /home/ubuntu/aegis && git pull origin main && docker compose -f docker-compose.prod.yml up -d --build --force-recreate 2>&1 | tail -10"
```

**Variants** (use when needed):

- **App-only rebuild** (faster, no DB restart):
  ```bash
  ssh -i ~/.ssh/gcloud_hermes ubuntu@143.47.112.212 "cd /home/ubuntu/aegis && docker compose -f docker-compose.prod.yml up -d --build --force-recreate api 2>&1 | tail -5"
  ```

- **Full rebuild with cache clear** (when Docker cache causes stale builds):
  ```bash
  ssh -i ~/.ssh/gcloud_hermes ubuntu@143.47.112.212 "cd /home/ubuntu/aegis && git pull origin main && docker compose -f docker-compose.prod.yml down --rmi local 2>/dev/null; docker compose -f docker-compose.prod.yml up -d --build --force-recreate"
  ```

- **Hard reset + deploy** (when local and remote diverge):
  ```bash
  ssh -i ~/.ssh/gcloud_hermes ubuntu@143.47.112.212 "cd /home/ubuntu/aegis && git fetch origin && git reset --hard origin/main && docker compose -f docker-compose.prod.yml up -d --build --force-recreate 2>&1 | tail -10"
  ```

### Step 3: Verify Deployment

Check containers are running and app responds:

```bash
ssh -i ~/.ssh/gcloud_hermes ubuntu@143.47.112.212 "docker ps --filter name=aegis --format '{{.Names}}: {{.Status}}' && curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8000/health"
```

Check for errors in app logs:

```bash
ssh -i ~/.ssh/gcloud_hermes ubuntu@143.47.112.212 "docker logs aegis-api 2>&1 | tail -20"
```

### Step 4: SCP Individual Files (hotfix without full rebuild)

When you need to push a single file fix without a full rebuild:

```bash
scp -i ~/.ssh/gcloud_hermes "LOCAL_PATH" ubuntu@143.47.112.212:REMOTE_PATH
```

Then restart the app container:

```bash
ssh -i ~/.ssh/gcloud_hermes ubuntu@143.47.112.212 "docker compose -f docker-compose.prod.yml up -d --force-recreate api"
```

## Important Notes

- **Do NOT touch the Kallion or Pixel projects** on the same server — they are separate deployments.
- **Docker Compose file** is always `docker-compose.prod.yml` (not the dev one).
- **SSH timeout**: Use `-o ConnectTimeout=120 -o ServerAliveCountMax=30 -o ServerAliveInterval=3` for long operations (builds).
- **LIVE trading is DISABLED by default** — never enable without explicit approval.
- **PostgreSQL port**: 5435 (internal only)
- **Redis port**: 6381 (internal only)
- **API port**: 8000 (internal only, proxied via nginx)

## Ports on Server

| Service | Internal Port | External |
|---|---|---|
| nginx (HTTP) | 80 | ✅ |
| nginx (HTTPS) | 443 | ✅ |
| AEGIS API | 8000 | ❌ (via nginx) |
| PostgreSQL | 5435 | ❌ (internal) |
| Redis | 6381 | ❌ (internal) |
| Kallion PostgreSQL | 5433 | ❌ (internal) |
| Pixel PostgreSQL | 5434 | ❌ (internal) |
| Pixel API | 5000 | ❌ (internal) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Build fails with errors | Check `docker logs aegis-api 2>&1 | tail -20` |
| App not responding after deploy | Check `docker logs aegis-api 2>&1 | tail -20` — check for startup errors |
| Containers not recreating | Use `--force-recreate` flag |
| Stale Docker cache | Use `--down --rmi local` variant |
| SSH connection timeout | Add `-o ConnectTimeout=120` flags |
| SSL issues | Run `sudo certbot certonly --webroot -w /var/www/aegis -d aegis.rotagov.com.br` |
