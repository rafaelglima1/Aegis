#!/bin/bash
# AEGIS V1.3 — Deploy Script for OCI Server
# Server: 143.47.112.212
# User: ubuntu
# SSH Key: ~/.ssh/gcloud_hermes

set -e

SERVER="ubuntu@143.47.112.212"
SSH_KEY="$HOME/.ssh/gcloud_hermes"
REMOTE_DIR="/home/ubuntu/aegis"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== AEGIS V1.3 Deployment ==="
echo "Server: $SERVER"
echo "Remote: $REMOTE_DIR"
echo ""

# Step 1: Create remote directory
echo "[1/6] Creating remote directory..."
ssh -i "$SSH_KEY" -o ConnectTimeout=15 "$SERVER" "mkdir -p $REMOTE_DIR"

# Step 2: Copy files to server
echo "[2/6] Copying files..."
scp -i "$SSH_KEY" -r "$PROJECT_DIR/Dockerfile" "$SERVER:$REMOTE_DIR/"
scp -i "$SSH_KEY" -r "$PROJECT_DIR/pyproject.toml" "$SERVER:$REMOTE_DIR/"
scp -i "$SSH_KEY" -r "$PROJECT_DIR/src" "$SERVER:$REMOTE_DIR/"
scp -i "$SSH_KEY" "$PROJECT_DIR/docker-compose.prod.yml" "$SERVER:$REMOTE_DIR/"
scp -i "$SSH_KEY" "$PROJECT_DIR/.env.prod" "$SERVER:$REMOTE_DIR/"

# Step 3: Copy nginx config
echo "[3/6] Copying nginx config..."
scp -i "$SSH_KEY" "$PROJECT_DIR/deploy/nginx-aegis.conf" "$SERVER:/tmp/nginx-aegis.conf"
ssh -i "$SSH_KEY" "$SERVER" "sudo cp /tmp/nginx-aegis.conf /etc/nginx/conf.d/aegis.conf"

# Step 4: Create .env.prod with secrets
echo "[4/6] Setting up environment..."
ssh -i "$SSH_KEY" "$SERVER" "cd $REMOTE_DIR && cat .env.prod | sed 's/\${POSTGRES_PASSWORD}/aegis_$(openssl rand -hex 16)/g' > .env.prod.tmp && mv .env.prod.tmp .env.prod"

# Step 5: Build and start containers
echo "[5/6] Building and starting containers..."
ssh -i "$SSH_KEY" -o ConnectTimeout=120 -o ServerAliveCountMax=30 -o ServerAliveInterval=3 "$SERVER" \
  "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml up -d --build --force-recreate 2>&1 | tail -15"

# Step 6: Verify deployment
echo "[6/6] Verifying deployment..."
sleep 5
ssh -i "$SSH_KEY" "$SERVER" "docker ps --filter name=aegis --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"

echo ""
echo "=== Deployment Complete ==="
echo "API: http://127.0.0.1:8000 (internal)"
echo "Domain: https://aegis.rotagov.com.br (after DNS + SSL setup)"
echo ""
echo "Next steps:"
echo "1. Point DNS aegis.rotagov.com.br → 143.47.112.212"
echo "2. Run: sudo certbot certonly --webroot -w /var/www/aegis -d aegis.rotagov.com.br"
echo "3. Reload nginx: sudo nginx -t && sudo systemctl reload nginx"
