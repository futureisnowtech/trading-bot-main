#!/bin/bash
set -e

# Configuration
SSH_PORT=2222

echo "Starting Droplet setup..."

# 1. Update system
apt-get update && apt-get upgrade -y

# 2. Install dependencies
apt-get install -y git docker.io docker-compose-v2 fail2ban ufw

# 3. Create algo-runner user and harden SSH
echo "Creating algo-runner user..."
if ! id "algo-runner" &>/dev/null; then
    adduser --disabled-password --gecos "" algo-runner
    usermod -aG docker algo-runner
    
    # Copy root's authorized_keys to algo-runner so the user doesn't lose access
    mkdir -p /home/algo-runner/.ssh
    cp /root/.ssh/authorized_keys /home/algo-runner/.ssh/
    chown -R algo-runner:algo-runner /home/algo-runner/.ssh
    chmod 700 /home/algo-runner/.ssh
    chmod 600 /home/algo-runner/.ssh/authorized_keys
    echo "  OK: algo-runner created and SSH keys synced."
else
    echo "  OK: algo-runner already exists."
fi

echo "Hardening SSH..."
sed -i "s/^#Port 22/Port $SSH_PORT/" /etc/ssh/sshd_config
# Ensure Port is set if not commented out
grep -q "^Port $SSH_PORT" /etc/ssh/sshd_config || echo "Port $SSH_PORT" >> /etc/ssh/sshd_config

sed -i "s/^#PasswordAuthentication yes/PasswordAuthentication no/" /etc/ssh/sshd_config
sed -i "s/^PasswordAuthentication yes/PasswordAuthentication no/" /etc/ssh/sshd_config
# Ensure PasswordAuthentication is set to no
grep -q "^PasswordAuthentication no" /etc/ssh/sshd_config || echo "PasswordAuthentication no" >> /etc/ssh/sshd_config

systemctl restart ssh

# 4. Configure Firewall
echo "Configuring UFW..."
ufw default deny incoming
ufw default allow outgoing
ufw allow $SSH_PORT/tcp
ufw --force enable

# 5. Fail2Ban
echo "Configuring Fail2Ban..."
systemctl enable fail2ban
systemctl start fail2ban

echo "Droplet setup complete. SSH is now on port $SSH_PORT. Password login disabled."
