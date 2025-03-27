# San-O1 Service Installation Guide

This guide provides step-by-step instructions for installing and configuring each service on its dedicated VM after creation with the `vm_deploy.sh` script.

## Common Initial Setup for All VMs

After creating the VMs using the `vm_deploy.sh` script, perform these steps for all service VMs:

1. Start the VM from the Proxmox web UI
2. Complete the Ubuntu Server installation process
3. Update the system:
   ```bash
   apt update && apt upgrade -y
   ```
4. Install common utilities:
   ```bash
   apt install -y curl wget git htop net-tools
   ```
5. Configure firewall (basic settings):
   ```bash
   apt install -y ufw
   ufw default deny incoming
   ufw default allow outgoing
   # Service-specific ports will be opened in their respective sections
   ```

## 1. Ollama VM Setup (with GPU)

### Prerequisites
- VM created with GPU passthrough configured as per [GPU_PASSTHROUGH_README.md](GPU_PASSTHROUGH_README.md)

### Install NVIDIA Drivers (for NVIDIA GPUs)
```bash
# Add NVIDIA repository
apt install -y software-properties-common
add-apt-repository -y ppa:graphics-drivers/ppa
apt update

# Install NVIDIA drivers and CUDA toolkit
apt install -y nvidia-driver-535 nvidia-cuda-toolkit

# Verify installation
nvidia-smi
```

### Install Ollama
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Create systemd service for automatic startup
cat > /etc/systemd/system/ollama.service << EOL
[Unit]
Description=Ollama AI Service
After=network.target

[Service]
ExecStart=/usr/bin/ollama serve
Restart=always
RestartSec=5
User=root
Environment=OLLAMA_HOST=0.0.0.0

[Install]
WantedBy=multi-user.target
EOL

# Enable and start service
systemctl enable ollama
systemctl start ollama

# Configure firewall
ufw allow 11434/tcp
ufw --force enable

# Pull default models (adjust as needed)
ollama pull deepseek:32b
```

### Test Ollama Installation
```bash
# Test with a simple query
curl -X POST http://localhost:11434/api/generate -d '{
  "model": "deepseek:32b",
  "prompt": "Why is GPU acceleration important for AI models?"
}'
```

## 2. Qdrant VM Setup

### Install Qdrant
```bash
# Install dependencies
apt install -y ca-certificates curl gnupg lsb-release

# Download and install Qdrant
curl -L https://github.com/qdrant/qdrant/releases/download/v1.8.0/qdrant-amd64.deb -o qdrant.deb
apt install -y ./qdrant.deb
rm qdrant.deb

# Configure Qdrant
mkdir -p /etc/qdrant
cat > /etc/qdrant/config.yaml << EOL
storage:
  storage_path: /var/lib/qdrant/storage
  snapshots_path: /var/lib/qdrant/snapshots
  on_disk_payload: true

service:
  host: 0.0.0.0
  http_port: 6333
  grpc_port: 6334

telemetry_disabled: true
EOL

# Create storage directories
mkdir -p /var/lib/qdrant/storage
mkdir -p /var/lib/qdrant/snapshots
chown -R qdrant:qdrant /var/lib/qdrant

# Enable and start service
systemctl enable --now qdrant

# Configure firewall
ufw allow 6333/tcp
ufw allow 6334/tcp
ufw --force enable
```

### Test Qdrant Installation
```bash
# Test API access
curl -X GET http://localhost:6333/collections
```

## 3. N8N VM Setup

### Install Node.js and N8N
```bash
# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
apt install -y nodejs

# Install n8n
npm install -g n8n

# Create systemd service
cat > /etc/systemd/system/n8n.service << EOL
[Unit]
Description=N8N Workflow Automation
After=network.target

[Service]
ExecStart=/usr/bin/n8n start
Restart=always
RestartSec=5
User=root
Environment=N8N_HOST=0.0.0.0
Environment=N8N_PORT=5678
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOL

# Enable and start service
systemctl enable n8n
systemctl start n8n

# Configure firewall
ufw allow 5678/tcp
ufw --force enable
```

### Test N8N Installation
```bash
# Check if N8N is running
curl http://localhost:5678
```

## 4. Redis VM Setup

### Install Redis
```bash
# Install Redis server
apt install -y redis-server

# Configure Redis to accept remote connections
sed -i 's/bind 127.0.0.1 ::1/bind 0.0.0.0/' /etc/redis/redis.conf
sed -i 's/protected-mode yes/protected-mode no/' /etc/redis/redis.conf

# Set a password for security
REDIS_PASSWORD=$(openssl rand -base64 24)
sed -i "s/# requirepass foobared/requirepass $REDIS_PASSWORD/" /etc/redis/redis.conf
echo "Redis password: $REDIS_PASSWORD" > /root/redis-password.txt
chmod 600 /root/redis-password.txt

# Restart Redis service
systemctl restart redis-server

# Configure firewall
ufw allow 6379/tcp
ufw --force enable
```

### Test Redis Installation
```bash
# Test Redis connection
redis-cli ping
redis-cli -a $REDIS_PASSWORD ping
```

## 5. PostgreSQL VM Setup

### Install PostgreSQL
```bash
# Install PostgreSQL
apt install -y postgresql postgresql-contrib

# Configure PostgreSQL to accept remote connections
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" /etc/postgresql/*/main/postgresql.conf

# Configure access control
cat > /etc/postgresql/*/main/pg_hba.conf << EOL
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             postgres                                peer
local   all             all                                     peer
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
host    all             all             0.0.0.0/0               scram-sha-256
EOL

# Create a database and user
POSTGRES_PASSWORD=$(openssl rand -base64 16)
sudo -u postgres psql -c "CREATE DATABASE sano1;"
sudo -u postgres psql -c "CREATE USER sano1admin WITH ENCRYPTED PASSWORD '$POSTGRES_PASSWORD';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE sano1 TO sano1admin;"
echo "PostgreSQL user: sano1admin" > /root/postgres-credentials.txt
echo "PostgreSQL password: $POSTGRES_PASSWORD" >> /root/postgres-credentials.txt
echo "PostgreSQL database: sano1" >> /root/postgres-credentials.txt
chmod 600 /root/postgres-credentials.txt

# Restart PostgreSQL
systemctl restart postgresql

# Configure firewall
ufw allow 5432/tcp
ufw --force enable
```

### Test PostgreSQL Installation
```bash
# Test database connection
PGPASSWORD=$POSTGRES_PASSWORD psql -h localhost -U sano1admin -d sano1 -c "SELECT version();"
```

## Integrating with Load Balancer

After setting up all service VMs, collect their IP addresses and update the load balancer configuration:

1. SSH into each VM to confirm its IP address:
   ```bash
   ip addr show
   ```

2. Note the IP addresses of all service VMs:
   - Ollama VM: [IP]
   - Qdrant VM: [IP]
   - N8N VM: [IP]
   - Redis VM: [IP]
   - PostgreSQL VM: [IP]

3. Run the `ha_load_balancer_setup.sh` script with the service IPs as environment variables:
   ```bash
   export OLLAMA_IP=192.168.1.x
   export QDRANT_IP=192.168.1.y
   export N8N_IP=192.168.1.z
   export REDIS_IP=192.168.1.a
   export POSTGRES_IP=192.168.1.b
   
   ./ha_load_balancer_setup.sh
   ```

4. After the load balancer VM is created, follow the steps provided in the script output to install HAProxy and Keepalived.

## Testing the Complete Setup

To test if everything is working properly:

1. Access HAProxy stats page:
   ```
   http://<load-balancer-ip>:8404/stats
   ```
   
2. Test Ollama through the load balancer:
   ```bash
   curl -X POST http://<load-balancer-ip>:11434/api/generate -d '{
     "model": "deepseek:32b",
     "prompt": "Testing load balanced Ollama instance"
   }'
   ```

3. Test Qdrant through the load balancer:
   ```bash
   curl -X GET http://<load-balancer-ip>:6333/collections
   ```

4. Test N8N through a web browser:
   ```
   http://<load-balancer-ip>:5678
   ```

## Maintenance Procedures

### Updating Services

#### Updating Ollama
```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl restart ollama
```

#### Updating Qdrant
```bash
curl -L https://github.com/qdrant/qdrant/releases/latest/download/qdrant-amd64.deb -o qdrant.deb
apt install -y ./qdrant.deb
systemctl restart qdrant
```

#### Updating N8N
```bash
npm update -g n8n
systemctl restart n8n
```

### Backing Up Services

#### PostgreSQL Backup
```bash
pg_dump -U postgres sano1 > /backup/sano1_$(date +%Y%m%d).sql
```

#### Redis Backup
```bash
redis-cli -a $REDIS_PASSWORD save
cp /var/lib/redis/dump.rdb /backup/redis_$(date +%Y%m%d).rdb
```

## Monitoring

For basic monitoring, install Prometheus and Node Exporter on each VM:

```bash
# Install Node Exporter
wget https://github.com/prometheus/node_exporter/releases/download/v1.5.0/node_exporter-1.5.0.linux-amd64.tar.gz
tar xvfz node_exporter-1.5.0.linux-amd64.tar.gz
cp node_exporter-1.5.0.linux-amd64/node_exporter /usr/local/bin/
useradd -rs /bin/false node_exporter

# Create systemd service
cat > /etc/systemd/system/node_exporter.service << EOL
[Unit]
Description=Node Exporter
After=network.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter

[Install]
WantedBy=multi-user.target
EOL

# Enable and start service
systemctl enable node_exporter
systemctl start node_exporter

# Allow Prometheus to scrape metrics
ufw allow 9100/tcp from <prometheus-server-ip>
```

## Conclusion

Following this guide, you should have a fully functional San-O1 infrastructure with:

- GPU-accelerated Ollama VM for AI inference
- Qdrant vector database for embeddings storage
- N8N workflow automation
- Redis in-memory database
- PostgreSQL relational database
- High-availability load balancer

All services are properly configured to work together, with the load balancer providing a unified access point to the infrastructure.
