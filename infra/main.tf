terraform {
  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.0"
    }
  }
}

variable "do_token" {
  description = "DigitalOcean API Token"
  type        = string
  sensitive   = true
}

variable "ssh_key_name" {
  description = "Name of the SSH key in DigitalOcean"
  type        = string
  default     = "algo-trading-key"
}

provider "digitalocean" {
  token = var.do_token
}

data "digitalocean_ssh_key" "main" {
  name = var.ssh_key_name
}

resource "digitalocean_droplet" "trading_bot" {
  image  = "ubuntu-24-04-x64"
  name   = "nyc3-algo-trading-bot"
  region = "nyc3"
  size   = "s-1vcpu-1gb" # NYC3 Droplet - matches current spec
  ssh_keys = [data.digitalocean_ssh_key.main.id]

  # User data to bootstrap the droplet (Cloud-Init)
  user_data = <<-EOF
    #!/bin/bash
    set -e

    # 1. Update and install dependencies
    apt-get update && apt-get upgrade -y
    apt-get install -y git docker.io docker-compose-v2 fail2ban ufw

    # 2. Create algo-runner user
    adduser --disabled-password --gecos "" algo-runner
    usermod -aG docker algo-runner
    
    # Sync root's SSH keys to algo-runner
    mkdir -p /home/algo-runner/.ssh
    cp /root/.ssh/authorized_keys /home/algo-runner/.ssh/
    chown -R algo-runner:algo-runner /home/algo-runner/.ssh
    chmod 700 /home/algo-runner/.ssh
    chmod 600 /home/algo-runner/.ssh/authorized_keys

    # 3. Configure Firewall
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 2222/tcp
    ufw allow 8080/tcp
    ufw allow 3000/tcp
    ufw --force enable

    # 4. Configure Fail2Ban
    systemctl enable fail2ban
    systemctl start fail2ban

    # 5. Change SSH Port to 2222
    sed -i 's/^#Port 22/Port 2222/' /etc/ssh/sshd_config
    systemctl restart ssh
  EOF

  tags = ["algo-trading", "production"]
}

output "droplet_ip" {
  value = digitalocean_droplet.trading_bot.ipv4_address
}
