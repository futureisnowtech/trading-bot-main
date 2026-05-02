#!/bin/bash
# -----------------------------------------------------------------------------
# SHADOW BRIDGE: Alpha-Sniper NYC3 Deployment Script
# -----------------------------------------------------------------------------
set -e

# Configuration
NYC_IP="64.225.20.38"
NYC_PORT="2222"
NYC_USER="root"
PROJECT_DIR="/root/algo_trading_final"
SSH_CMD="ssh -p $NYC_PORT -i ~/.ssh/id_ed25519"

echo "🚀 Starting Shadow Bridge Deployment..."

# 1. Force Git Sync
echo "📦 Committing and pushing local changes..."
git add .
git commit -m "ALPHA-SNIPER: Production hot-reload $(date +'%Y-%m-%d %H:%M:%S')" || echo "No changes to commit"
git push origin feature/v10-rebuild

# 2. Update NYC3 Code
echo "📡 Syncing code on NYC3 Droplet..."
$SSH_CMD $NYC_USER@$NYC_IP << EOF
    cd $PROJECT_DIR || git clone git@github.com:futureisnowtech/trading-bot-main.git $PROJECT_DIR
    cd $PROJECT_DIR
    git fetch origin
    git checkout feature/v10-rebuild
    git pull origin feature/v10-rebuild
    
    # 3. Environment Configuration
    echo "⚙️ Setting ENV=PROD on NYC..."
    sed -i 's/ENV=LOCAL/ENV=PROD/g' .env || echo "ENV=PROD" >> .env
    sed -i 's/PAPER_TRADING=true/PAPER_TRADING=false/g' .env
    
    # 4. IP Whitelisting (Optional)
    MAC_IP=\$(grep MAC_IP .env | cut -d '=' -f2)
    if [ ! -z "\$MAC_IP" ]; then
        echo "🛡️ Whitelisting Mac IP: \$MAC_IP for Grafana (3000)..."
        ufw allow from \$MAC_IP to any port 3000 proto tcp
        ufw allow from \$MAC_IP to any port 9090 proto tcp # Prometheus
    fi
    
    # 5. Zero-Downtime Hot-Reload
    echo "🔥 Hot-reloading Docker stack..."
    docker compose up -d --build --remove-orphans
    
    # 5. Health Check
    echo "🏥 Running health check..."
    sleep 10
    docker ps | grep algo-bot-live
EOF

echo "✅ Deployment Successful. NYC3 is now LIVE."
