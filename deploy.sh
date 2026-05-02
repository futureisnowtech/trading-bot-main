#!/bin/bash
# -----------------------------------------------------------------------------
# SHADOW BRIDGE: Alpha-Sniper NYC3 Deployment Script
# -----------------------------------------------------------------------------
set -e

# Configuration
NYC_IP="64.225.20.38"
NYC_PORT="2222"
NYC_USER="root"
PROJECT_DIR="/root/bot"
SSH_CMD="ssh -p $NYC_PORT"

echo "🚀 Starting Shadow Bridge Deployment (RSYNC Mode)..."

# 1. Force Git Push (for backup)
echo "📦 Backing up changes to GitHub..."
git add .
git commit -m "ALPHA-SNIPER: Production hot-reload $(date +'%Y-%m-%d %H:%M:%S')" || echo "No changes to commit"
git push origin feature/v10-rebuild || echo "GitHub push failed, proceeding with direct sync..."

# 2. Update NYC3 Code via RSYNC
echo "📡 Syncing code on NYC3 Droplet via rsync..."
rsync -avz -e "ssh -p $NYC_PORT" --exclude '.git' --exclude '__pycache__' --exclude 'logs' --exclude '.pytest_cache' . $NYC_USER@$NYC_IP:$PROJECT_DIR/

# 3. Configure and Restart on Server
$SSH_CMD $NYC_USER@$NYC_IP << EOF
    cd $PROJECT_DIR
    
    # Environment Configuration (Sanity check)
    echo "⚙️ Ensuring ENV=PROD and PAPER=false..."
    sed -i 's/PAPER_TRADING=true/PAPER_TRADING=false/g' .env
    
    # 5. Zero-Downtime Hot-Reload
    echo "🔥 Hot-reloading Docker stack..."
    docker compose up -d --build --remove-orphans
    
    # 6. Health Check
    echo "🏥 Running health check..."
    sleep 10
    docker ps | grep algo-bot-live
    
    # 7. Grafana Final Provisioning
    echo "📊 Finalizing Grafana Dashboards..."
    docker exec algo-bot-live python3 provision_grafana_final.py
EOF

echo "✅ Deployment Successful. NYC3 is now LIVE."
