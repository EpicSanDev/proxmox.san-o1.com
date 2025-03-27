#!/usr/bin/env python3
"""
Service Deployer Module
Handles deployment of AI infrastructure services to Proxmox nodes.
"""

import os
import time
import logging
import random
from string import ascii_letters, digits

logger = logging.getLogger('san-o1-deployer.service_deployer')

class ServiceDeployer:
    """Deploys services to Proxmox nodes based on allocations."""
    
    def __init__(self, proxmox, config):
        """Initialize with Proxmox API and service configuration."""
        self.proxmox = proxmox
        self.config = config
        self.templates = config.get('templates', {})
        self.default_storage = config.get('default_storage', 'local')
        self.network = config.get('network', {})
        self.base_vmid = config.get('base_vmid', 1000)
        # Load specific service configurations
        self.services = {
            'qdrant': config.get('qdrant', {}),
            'ollama': config.get('ollama', {}),
            'n8n': config.get('n8n', {}),
            'redis': config.get('redis', {}),
            'postgres': config.get('postgres', {})
        }
        self.results = {}
    
    def get_best_storage_for_node(self, node, service_name):
        """Find the best storage on a node, preferring ZFS with most free space.
        
        Args:
            node (str): Proxmox node name
            service_name (str): Service name for logging
            
        Returns:
            str: Selected storage name
        """
        try:
            # Get configured storage for this service as a fallback
            configured_storage = self.services.get(service_name, {}).get('storage', self.default_storage)
            
            # Get all available storages on the node
            storage_list = self.proxmox.get_storage(node)
            if not storage_list:
                logger.info(f"No storages found on node {node}, using configured storage: {configured_storage}")
                return configured_storage
                
            # First, try to find ZFS storages
            zfs_storages = []
            for s in storage_list:
                if 'zfs' in s.get('type', '').lower():
                    # Calculate free space
                    total = s.get('total', 0)
                    used = s.get('used', 0)
                    free = total - used
                    zfs_storages.append((s['storage'], free))
            
            # Sort ZFS storages by free space (most free space first)
            zfs_storages.sort(key=lambda x: x[1], reverse=True)
            
            # Use the ZFS storage with the most free space if available
            if zfs_storages:
                storage = zfs_storages[0][0]
                logger.info(f"Selected ZFS storage '{storage}' with {zfs_storages[0][1]} free space for {service_name} on node {node}")
                return storage
            
            # If no ZFS storages, find the storage with most free space
            all_storages = []
            for s in storage_list:
                if s.get('type') != 'dir':  # Skip 'dir' type storage which isn't useful for containers
                    total = s.get('total', 0)
                    used = s.get('used', 0)
                    free = total - used
                    all_storages.append((s['storage'], free))
            
            # Sort all storages by free space
            all_storages.sort(key=lambda x: x[1], reverse=True)
            
            if all_storages:
                storage = all_storages[0][0]
                logger.info(f"No ZFS storage found, using storage '{storage}' with {all_storages[0][1]} free space for {service_name} on node {node}")
                return storage
                
            # If we couldn't find any suitable storage, use the configured one
            logger.warning(f"Could not find suitable storage on node {node}, using configured storage: {configured_storage}")
            return configured_storage
            
        except Exception as e:
            # Fall back to configured storage if there's an error
            configured_storage = self.services.get(service_name, {}).get('storage', self.default_storage)
            logger.warning(f"Error selecting storage for {service_name} on node {node}: {str(e)}. Using default: {configured_storage}")
            return configured_storage
    
    def generate_password(self, length=16):
        """Generate a secure random password."""
        chars = ascii_letters + digits + '!@#$%^&*()-_=+'
        return ''.join(random.choice(chars) for _ in range(length))
    
    # Track VMIDs used in this deployment session to avoid conflicts
    _used_vmids = set()
    
    def get_next_vmid(self):
        """Get the next available VMID."""
        # Get all existing VMs and containers across all nodes
        next_id = self.base_vmid
        used_ids = set(self._used_vmids)  # Start with already used VMIDs in this session
        
        for node in self.proxmox.get_nodes():
            node_name = node['node']
            try:
                # Get VMs
                for vm in self.proxmox.get_qemu_vms(node_name):
                    used_ids.add(vm['vmid'])
                
                # Get containers
                for ct in self.proxmox.get_lxc_containers(node_name):
                    used_ids.add(ct['vmid'])
            except Exception as e:
                logger.warning(f"Couldn't get VM/container list for node {node_name}: {str(e)}")
        
        # Find the next available ID
        while next_id in used_ids:
            next_id += 1
        
        # Remember this VMID is now used
        self._used_vmids.add(next_id)
        
        return next_id
    
    def deploy_services(self, allocations):
        """Deploy services to allocated nodes."""
        results = {}
        
        for service_name, node in allocations.items():
            logger.info(f"Deploying service {service_name} to node {node}")
            
            try:
                # Handle specific service types
                if service_name == 'qdrant':
                    results[service_name] = self.deploy_qdrant(node)
                elif service_name == 'ollama':
                    results[service_name] = self.deploy_ollama(node)
                elif service_name == 'n8n':
                    results[service_name] = self.deploy_n8n(node)
                elif service_name == 'redis':
                    results[service_name] = self.deploy_redis(node)
                elif service_name == 'postgres':
                    results[service_name] = self.deploy_postgres(node)
                else:
                    logger.warning(f"Unknown service type: {service_name}")
            except Exception as e:
                logger.error(f"Error deploying {service_name} on {node}: {str(e)}")
                results[service_name] = {'error': str(e), 'node': node}
        
        self.results = results
        return results
    
    def deploy_qdrant(self, node):
        """Deploy Qdrant vector database container."""
        vmid = self.get_next_vmid()
        hostname = f"qdrant-{vmid}"
        
        # Get template settings with defaults
        template = self.templates.get('qdrant', self.templates.get('default', {}))
        
        # Find the best storage for this node
        storage = self.get_best_storage_for_node(node, 'qdrant')
        
        # Base config for container
        config = {
            'vmid': vmid,  # Add VMID to the request parameters
            'ostemplate': template.get('ostemplate', 'local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst'),
            'arch': 'amd64',
            'cores': self.services['qdrant'].get('cores', 4),
            'memory': self.services['qdrant'].get('memory', 8192),
            'swap': self.services['qdrant'].get('swap', 2048),
            'storage': storage,
            'rootfs': f"{storage}:{self.services['qdrant'].get('disk_size', 50)}G",
            'net0': f"name=eth0,bridge={self.network.get('bridge', 'vmbr0')},ip=dhcp",
            'hostname': hostname,
            'unprivileged': 1,
            'features': 'nesting=1',
            'start': 1,
            'onboot': 1,
            'description': f"Qdrant vector database - Deployed by san-o1-deployer"
        }
        
        # Create container
        logger.info(f"Creating Qdrant container on node {node} with VMID {vmid}")
        task_id = self.proxmox.create_lxc_container(node, config)
        logger.debug(f"Container creation task ID: {task_id}")
        
        # Wait for container creation to complete - no need to wait as the API is synchronous
        # self.proxmox.wait_for_task(node, task_id)
        
        # Start container if not already started
        try:
            self.proxmox.start_lxc_container(node, vmid)
            logger.info(f"Started Qdrant container {vmid}")
        except Exception as e:
            logger.warning(f"Could not start container, it might already be running: {str(e)}")
        
        # Setup script for Qdrant
        setup_script = """
#!/bin/bash
set -e

# Update and install dependencies
apt-get update
apt-get install -y curl apt-transport-https ca-certificates gnupg lsb-release

# Install Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose

# Configure Docker to start on boot
systemctl enable docker
systemctl start docker

# Create Qdrant configuration directory
mkdir -p /etc/qdrant
mkdir -p /var/lib/qdrant

# Create docker-compose.yml
cat > /root/docker-compose.yml << 'EOL'
version: '3'
services:
  qdrant:
    image: qdrant/qdrant:latest
    container_name: qdrant
    restart: always
    volumes:
      - /var/lib/qdrant:/qdrant_storage
    ports:
      - "6333:6333"
      - "6334:6334"
    environment:
      - QDRANT_ALLOW_CORS=true
EOL

# Start Qdrant
cd /root
docker-compose up -d

echo "Qdrant deployment complete!"
"""
        
        # Execute setup script in container
        try:
            logger.info(f"Executing Qdrant setup script in container {vmid} on node {node}")
            self.proxmox.execute_script_in_lxc(node, vmid, setup_script)
            logger.info(f"Qdrant setup script executed successfully in container {vmid}")
        except Exception as e:
            logger.error(f"Failed to execute Qdrant setup script in container {vmid}: {str(e)}")
        
        # Return deployment information
        result = {
            'node': node,
            'id': vmid,
            'hostname': hostname,
            'service': 'qdrant',
            'access_url': f"http://{hostname}:6333" 
        }
        
        logger.info(f"Qdrant deployment complete: VMID {vmid} on {node}")
        return result
    
    def deploy_ollama(self, node):
        """Deploy Ollama with deepseek:32B model on NVIDIA-enabled node."""
        vmid = self.get_next_vmid()
        hostname = f"ollama-{vmid}"
        
        # Get template settings with defaults
        template = self.templates.get('ollama', self.templates.get('default', {}))
        
        # Find the best storage for this node
        storage = self.get_best_storage_for_node(node, 'ollama')
        
        # Base config for container - using Privileged mode for GPU access
        config = {
            'vmid': vmid,  # Add VMID to the request parameters
            'ostemplate': template.get('ostemplate', 'local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst'),
            'arch': 'amd64',
            'cores': self.services['ollama'].get('cores', 8),
            'memory': self.services['ollama'].get('memory', 32768),  # 32GB for large model
            'swap': self.services['ollama'].get('swap', 8192),
            'storage': storage,
            'rootfs': f"{storage}:{self.services['ollama'].get('disk_size', 100)}G",
            'net0': f"name=eth0,bridge={self.network.get('bridge', 'vmbr0')},ip=dhcp",
            'hostname': hostname,
            # Need privileged for GPU passthrough
            'unprivileged': 0,
            'features': 'nesting=1',
            'start': 1,
            'onboot': 1,
            'description': f"Ollama with deepseek:32B model - Deployed by san-o1-deployer"
        }
        
        # Create container
        logger.info(f"Creating Ollama container on node {node} with VMID {vmid}")
        task_id = self.proxmox.create_lxc_container(node, config)
        logger.debug(f"Container creation task ID: {task_id}")
        
        # Wait for container creation to complete - no need to wait as the API is synchronous
        # self.proxmox.wait_for_task(node, task_id)
        
        # Start container if not already started
        try:
            self.proxmox.start_lxc_container(node, vmid)
            logger.info(f"Started Ollama container {vmid}")
        except Exception as e:
            logger.warning(f"Could not start container, it might already be running: {str(e)}")
        
        # Setup script for Ollama with GPU support
        setup_script = """
#!/bin/bash
set -e

# Update and install dependencies
apt-get update
apt-get install -y curl apt-transport-https ca-certificates gnupg lsb-release

# Install NVIDIA drivers and container toolkit
apt-get install -y nvidia-driver-535 nvidia-utils-535
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | tee /etc/apt/sources.list.d/nvidia-docker.list
apt-get update
apt-get install -y nvidia-container-toolkit

# Install Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose

# Configure Docker to use NVIDIA
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << EOL
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  }
}
EOL

# Configure Docker to start on boot
systemctl enable docker
systemctl restart docker

# Create docker-compose.yml for Ollama
cat > /root/docker-compose.yml << 'EOL'
version: '3'
services:
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    restart: always
    ports:
      - "11434:11434"
    volumes:
      - /var/lib/ollama:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    environment:
      - OLLAMA_MODELS=/root/.ollama/models
EOL

# Start Ollama
mkdir -p /var/lib/ollama
cd /root
docker-compose up -d

# Pull deepseek:32B model
sleep 10
docker exec -it ollama ollama pull deepseek:32b

echo "Ollama deployment complete!"
"""
        
        # Execute setup script in container
        try:
            logger.info(f"Executing Ollama setup script in container {vmid} on node {node}")
            self.proxmox.execute_script_in_lxc(node, vmid, setup_script)
            logger.info(f"Ollama setup script executed successfully in container {vmid}")
        except Exception as e:
            logger.error(f"Failed to execute Ollama setup script in container {vmid}: {str(e)}")
        
        # Return deployment information
        result = {
            'node': node,
            'id': vmid,
            'hostname': hostname,
            'service': 'ollama',
            'access_url': f"http://{hostname}:11434"
        }
        
        logger.info(f"Ollama deployment complete: VMID {vmid} on {node}")
        return result
    
    def deploy_n8n(self, node):
        """Deploy n8n workflow automation platform."""
        vmid = self.get_next_vmid()
        hostname = f"n8n-{vmid}"
        
        # Get template settings with defaults
        template = self.templates.get('n8n', self.templates.get('default', {}))
        
        # Find the best storage for this node
        storage = self.get_best_storage_for_node(node, 'n8n')
        
        # Base config for container
        config = {
            'vmid': vmid,  # Add VMID to the request parameters
            'ostemplate': template.get('ostemplate', 'local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst'),
            'arch': 'amd64',
            'cores': self.services['n8n'].get('cores', 2),
            'memory': self.services['n8n'].get('memory', 2048),
            'swap': self.services['n8n'].get('swap', 1024),
            'storage': storage,
            'rootfs': f"{storage}:{self.services['n8n'].get('disk_size', 20)}G",
            'net0': f"name=eth0,bridge={self.network.get('bridge', 'vmbr0')},ip=dhcp",
            'hostname': hostname,
            'unprivileged': 1,
            'features': 'nesting=1',
            'start': 1,
            'onboot': 1,
            'description': f"n8n workflow automation platform - Deployed by san-o1-deployer"
        }
        
        # Create container
        logger.info(f"Creating n8n container on node {node} with VMID {vmid}")
        task_id = self.proxmox.create_lxc_container(node, config)
        logger.debug(f"Container creation task ID: {task_id}")
        
        # Wait for container creation to complete - no need to wait as the API is synchronous
        # self.proxmox.wait_for_task(node, task_id)
        
        # Start container if not already started
        try:
            self.proxmox.start_lxc_container(node, vmid)
            logger.info(f"Started n8n container {vmid}")
        except Exception as e:
            logger.warning(f"Could not start container, it might already be running: {str(e)}")
        
        # Setup script for n8n
        setup_script = """
#!/bin/bash
set -e

# Update and install dependencies
apt-get update
apt-get install -y curl apt-transport-https ca-certificates gnupg lsb-release

# Install Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose

# Configure Docker to start on boot
systemctl enable docker
systemctl start docker

# Create n8n configuration directory
mkdir -p /var/lib/n8n

# Create docker-compose.yml
cat > /root/docker-compose.yml << 'EOL'
version: '3'
services:
  n8n:
    image: n8nio/n8n:latest
    container_name: n8n
    restart: always
    ports:
      - "5678:5678"
    volumes:
      - /var/lib/n8n:/home/node/.n8n
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=%%PASSWORD%%
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - N8N_PROTOCOL=http
      - NODE_ENV=production
EOL

# Generate secure password and insert it
PASSWORD=$(openssl rand -base64 12)
sed -i "s/%%PASSWORD%%/$PASSWORD/g" /root/docker-compose.yml

# Save password for reference
echo "n8n admin password: $PASSWORD" > /root/n8n_password.txt
chmod 600 /root/n8n_password.txt

# Start n8n
cd /root
docker-compose up -d

echo "n8n deployment complete!"
"""
        
        # Execute setup script in container
        try:
            logger.info(f"Executing n8n setup script in container {vmid} on node {node}")
            self.proxmox.execute_script_in_lxc(node, vmid, setup_script)
            logger.info(f"n8n setup script executed successfully in container {vmid}")
        except Exception as e:
            logger.error(f"Failed to execute n8n setup script in container {vmid}: {str(e)}")
        
        # Generate a password for n8n
        n8n_password = self.generate_password()
        
        # Return deployment information
        result = {
            'node': node,
            'id': vmid,
            'hostname': hostname,
            'service': 'n8n',
            'access_url': f"http://{hostname}:5678",
            'credentials': {
                'username': 'admin',
                'password': n8n_password
            }
        }
        
        logger.info(f"n8n deployment complete: VMID {vmid} on {node}")
        return result
    
    def deploy_redis(self, node):
        """Deploy Redis in-memory database."""
        vmid = self.get_next_vmid()
        hostname = f"redis-{vmid}"
        
        # Get template settings with defaults
        template = self.templates.get('redis', self.templates.get('default', {}))
        
        # Find the best storage for this node
        storage = self.get_best_storage_for_node(node, 'redis')
        
        # Base config for container
        config = {
            'vmid': vmid,  # Add VMID to the request parameters
            'ostemplate': template.get('ostemplate', 'local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst'),
            'arch': 'amd64',
            'cores': self.services['redis'].get('cores', 2),
            'memory': self.services['redis'].get('memory', 4096),
            'swap': self.services['redis'].get('swap', 1024),
            'storage': storage,
            'rootfs': f"{storage}:{self.services['redis'].get('disk_size', 10)}G",
            'net0': f"name=eth0,bridge={self.network.get('bridge', 'vmbr0')},ip=dhcp",
            'hostname': hostname,
            'unprivileged': 1,
            'features': 'nesting=1',
            'start': 1,
            'onboot': 1,
            'description': f"Redis in-memory database - Deployed by san-o1-deployer"
        }
        
        # Create container
        logger.info(f"Creating Redis container on node {node} with VMID {vmid}")
        task_id = self.proxmox.create_lxc_container(node, config)
        logger.debug(f"Container creation task ID: {task_id}")
        
        # Wait for container creation to complete - no need to wait as the API is synchronous
        # self.proxmox.wait_for_task(node, task_id)
        
        # Start container if not already started
        try:
            self.proxmox.start_lxc_container(node, vmid)
            logger.info(f"Started Redis container {vmid}")
        except Exception as e:
            logger.warning(f"Could not start container, it might already be running: {str(e)}")
        
        # Setup script for Redis
        redis_password = self.generate_password()
        setup_script = f"""
#!/bin/bash
set -e

# Update and install dependencies
apt-get update
apt-get install -y curl apt-transport-https ca-certificates gnupg lsb-release

# Install Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose

# Configure Docker to start on boot
systemctl enable docker
systemctl start docker

# Create Redis config directory and data volume
mkdir -p /etc/redis
mkdir -p /var/lib/redis

# Create Redis config 
cat > /etc/redis/redis.conf << 'EOL'
bind 0.0.0.0
protected-mode yes
port 6379
tcp-backlog 511
requirepass {redis_password}
timeout 0
tcp-keepalive 300
daemonize no
supervised no
pidfile /var/run/redis_6379.pid
loglevel notice
logfile ""
databases 16
save 900 1
save 300 10
save 60 10000
stop-writes-on-bgsave-error yes
rdbcompression yes
rdbchecksum yes
dbfilename dump.rdb
dir /data
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec
EOL

# Create docker-compose.yml
cat > /root/docker-compose.yml << 'EOL'
version: '3'
services:
  redis:
    image: redis:latest
    container_name: redis
    restart: always
    command: redis-server /usr/local/etc/redis/redis.conf
    ports:
      - "6379:6379"
    volumes:
      - /etc/redis/redis.conf:/usr/local/etc/redis/redis.conf
      - /var/lib/redis:/data
EOL

# Save password for reference
echo "Redis password: {redis_password}" > /root/redis_password.txt
chmod 600 /root/redis_password.txt

# Start Redis
cd /root
docker-compose up -d

echo "Redis deployment complete!"
"""
        
        # Execute setup script in container
        try:
            logger.info(f"Executing Redis setup script in container {vmid} on node {node}")
            self.proxmox.execute_script_in_lxc(node, vmid, setup_script)
            logger.info(f"Redis setup script executed successfully in container {vmid}")
        except Exception as e:
            logger.error(f"Failed to execute Redis setup script in container {vmid}: {str(e)}")
        
        # Return deployment information
        result = {
            'node': node,
            'id': vmid,
            'hostname': hostname,
            'service': 'redis',
            'access_url': f"redis://{hostname}:6379",
            'credentials': {
                'password': redis_password
            }
        }
        
        logger.info(f"Redis deployment complete: VMID {vmid} on {node}")
        return result
    
    def deploy_postgres(self, node):
        """Deploy PostgreSQL database."""
        vmid = self.get_next_vmid()
        hostname = f"postgres-{vmid}"
        
        # Get template settings with defaults
        template = self.templates.get('postgres', self.templates.get('default', {}))
        
        # Find the best storage for this node
        storage = self.get_best_storage_for_node(node, 'postgres')
        
        # Base config for container
        config = {
            'vmid': vmid,  # Add VMID to the request parameters
            'ostemplate': template.get('ostemplate', 'local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst'),
            'arch': 'amd64',
            'cores': self.services['postgres'].get('cores', 2),
            'memory': self.services['postgres'].get('memory', 4096),
            'swap': self.services['postgres'].get('swap', 1024),
            'storage': storage,
            'rootfs': f"{storage}:{self.services['postgres'].get('disk_size', 20)}G",
            'net0': f"name=eth0,bridge={self.network.get('bridge', 'vmbr0')},ip=dhcp",
            'hostname': hostname,
            'unprivileged': 1,
            'features': 'nesting=1',
            'start': 1,
            'onboot': 1,
            'description': f"PostgreSQL database - Deployed by san-o1-deployer"
        }
        
        # Create container
        logger.info(f"Creating PostgreSQL container on node {node} with VMID {vmid}")
        task_id = self.proxmox.create_lxc_container(node, config)
        logger.debug(f"Container creation task ID: {task_id}")
        
        # Wait for container creation to complete - no need to wait as the API is synchronous
        # self.proxmox.wait_for_task(node, task_id)
        
        # Start container if not already started
        try:
            self.proxmox.start_lxc_container(node, vmid)
            logger.info(f"Started PostgreSQL container {vmid}")
        except Exception as e:
            logger.warning(f"Could not start container, it might already be running: {str(e)}")
        
        # Generate PostgreSQL credentials
        postgres_password = self.generate_password()
        
        # Setup script for PostgreSQL
        setup_script = f"""
#!/bin/bash
set -e

# Update and install dependencies
apt-get update
apt-get install -y curl apt-transport-https ca-certificates gnupg lsb-release

# Install Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose

# Configure Docker to start on boot
systemctl enable docker
systemctl start docker

# Create PostgreSQL data directory
mkdir -p /var/lib/postgresql

# Create docker-compose.yml
cat > /root/docker-compose.yml << 'EOL'
version: '3'
services:
  postgres:
    image: postgres:15
    container_name: postgres
    restart: always
    environment:
      - POSTGRES_PASSWORD={postgres_password}
      - POSTGRES_USER=postgres
      - PGDATA=/var/lib/postgresql/data
    volumes:
      - /var/lib/postgresql:/var/lib/postgresql/data
    ports:
      - "5432:5432"
EOL

# Save credentials for reference
echo "PostgreSQL username: postgres" > /root/postgres_credentials.txt
echo "PostgreSQL password: {postgres_password}" >> /root/postgres_credentials.txt
chmod 600 /root/postgres_credentials.txt

# Start PostgreSQL
cd /root
docker-compose up -d

echo "PostgreSQL deployment complete!"
"""
        
        # Execute setup script in container
        try:
            logger.info(f"Executing PostgreSQL setup script in container {vmid} on node {node}")
            self.proxmox.execute_script_in_lxc(node, vmid, setup_script)
            logger.info(f"PostgreSQL setup script executed successfully in container {vmid}")
        except Exception as e:
            logger.error(f"Failed to execute PostgreSQL setup script in container {vmid}: {str(e)}")
        
        # Return deployment information
        result = {
            'node': node,
            'id': vmid,
            'hostname': hostname,
            'service': 'postgres',
            'access_url': f"postgresql://postgres@{hostname}:5432/postgres",
            'credentials': {
                'username': 'postgres',
                'password': postgres_password
            }
        }
        
        logger.info(f"PostgreSQL deployment complete: VMID {vmid} on {node}")
        return result
