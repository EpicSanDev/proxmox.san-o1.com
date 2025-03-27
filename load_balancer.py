#!/usr/bin/env python3
"""
Load Balancer Module
Handles load balancing configuration for AI infrastructure services.
"""

import logging
import random
from string import ascii_letters, digits

logger = logging.getLogger('san-o1-deployer.load_balancer')

class LoadBalancer:
    """Configure and manage load balancing for services."""
    
    def __init__(self, proxmox, config):
        """Initialize with Proxmox API and load balancer configuration."""
        self.proxmox = proxmox
        self.config = config
        self.lb_node = config.get('node', None)  # Node for load balancer deployment
        self.ha_enabled = config.get('ha_enabled', False)  # High availability
        self.ssl_enabled = config.get('ssl_enabled', False)  # SSL termination
        self.default_storage = config.get('default_storage', 'local')
        self.network = config.get('network', {})
        self.base_vmid = config.get('base_vmid', 2000)  # Start from a different range than services
        self.domain = config.get('domain', 'ai-cluster.local')
        self.lb_config = {}
    
    def generate_password(self, length=16):
        """Generate a secure random password."""
        chars = ascii_letters + digits + '!@#$%^&*()-_=+'
        return ''.join(random.choice(chars) for _ in range(length))
    
    # Track VMIDs used in this deployment session to avoid conflicts
    _used_vmids = set()
    
    def get_next_vmid(self):
        """Get the next available VMID."""
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
    
    def select_load_balancer_node(self, deployment_results):
        """Select the best node for deploying the load balancer."""
        if self.lb_node:
            return self.lb_node
        
        # Count services per node
        node_counts = {}
        for service, info in deployment_results.items():
            node = info.get('node')
            if node:
                node_counts[node] = node_counts.get(node, 0) + 1
        
        # Select node with most services to minimize network hops
        if node_counts:
            return max(node_counts.items(), key=lambda x: x[1])[0]
        
        # Fallback: get the first available node
        try:
            nodes = self.proxmox.get_nodes()
            if nodes:
                return nodes[0]['node']
        except Exception as e:
            logger.error(f"Error getting Proxmox nodes: {str(e)}")
        
        # Final fallback
        return 'pve'
    
    def configure(self, deployment_results):
        """Configure load balancing for deployed services."""
        # Skip if no services to balance
        if not deployment_results:
            logger.warning("No services to configure load balancing for")
            return {}
        
        # Select node for load balancer deployment
        lb_node = self.select_load_balancer_node(deployment_results)
        logger.info(f"Selected node {lb_node} for load balancer deployment")
        
        # Check if we should deploy a load balancer
        if self.config.get('deploy_lb', True):
            self.lb_config = self.deploy_load_balancer(lb_node, deployment_results)
        else:
            logger.info("Load balancer deployment is disabled")
            self.lb_config = self.generate_lb_config(deployment_results)
        
        return self.lb_config
    
    def generate_lb_config(self, deployment_results):
        """Generate configuration for load balancing without deploying."""
        lb_config = {
            'services': {}
        }
        
        # Group services by type for potential clustering
        service_groups = {}
        for service_name, info in deployment_results.items():
            service_type = info.get('service', 'unknown')
            if service_type not in service_groups:
                service_groups[service_type] = []
            service_groups[service_type].append(info)
        
        # Generate configuration for each service type
        for service_type, instances in service_groups.items():
            # For now, just take the first instance as we don't have clustering
            if instances:
                instance = instances[0]
                hostname = instance.get('hostname', 'unknown')
                lb_config['services'][service_type] = {
                    'hostname': hostname,
                    'access_url': instance.get('access_url', ''),
                    'instances': [{'hostname': i.get('hostname'), 'node': i.get('node')} for i in instances]
                }
        
        return lb_config
    
    def deploy_load_balancer(self, node, deployment_results):
        """Deploy a load balancer container."""
        vmid = self.get_next_vmid()
        hostname = f"lb-{vmid}"
        
        # Get template settings with defaults
        storage = self.config.get('storage', self.default_storage)
        
        # Base config for container
        config = {
            'vmid': vmid,  # Add VMID parameter
            'ostemplate': 'local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst',
            'arch': 'amd64',
            'cores': self.config.get('cores', 2),
            'memory': self.config.get('memory', 2048),
            'swap': self.config.get('swap', 1024),
            'storage': storage,
            'rootfs': f"{storage}:{self.config.get('disk_size', 10)}G",
            'net0': f"name=eth0,bridge={self.network.get('bridge', 'vmbr0')},ip=dhcp",
            'hostname': hostname,
            'unprivileged': 1,
            'features': 'nesting=1',
            'start': 1,
            'onboot': 1,
            'description': f"HAProxy Load Balancer - Deployed by san-o1-deployer"
        }
        
        # Create container
        logger.info(f"Creating load balancer container on node {node} with VMID {vmid}")
        task_id = self.proxmox.create_lxc_container(node, config)
        logger.debug(f"Container creation task ID: {task_id}")
        
        # Wait for container creation to complete - no need to wait as the API is synchronous
        # self.proxmox.wait_for_task(node, task_id)
        
        # Start container if not already started
        try:
            self.proxmox.start_lxc_container(node, vmid)
            logger.info(f"Started load balancer container {vmid}")
        except Exception as e:
            logger.warning(f"Could not start container, it might already be running: {str(e)}")
        
        # Group services by type for load balancing
        service_groups = {}
        for service_name, info in deployment_results.items():
            service_type = info.get('service', 'unknown')
            if service_type not in service_groups:
                service_groups[service_type] = []
            service_groups[service_type].append(info)
        
        # Generate HAProxy configuration
        haproxy_cfg = self.generate_haproxy_config(service_groups)
        
        # Admin password for HAProxy stats
        stats_password = self.generate_password(12)
        
        # Setup script for HAProxy
        setup_script = f"""
#!/bin/bash
set -e

# Update and install dependencies
apt-get update
apt-get install -y haproxy certbot

# Create HAProxy configuration
cat > /etc/haproxy/haproxy.cfg << 'EOL'
{haproxy_cfg}
EOL

# Enable HAProxy
systemctl enable haproxy
systemctl restart haproxy

# Save stats password for reference
echo "HAProxy stats username: admin" > /root/haproxy_stats.txt
echo "HAProxy stats password: {stats_password}" >> /root/haproxy_stats.txt
chmod 600 /root/haproxy_stats.txt

echo "HAProxy Load Balancer deployment complete!"
"""
        
        # Execute setup script in container
        try:
            logger.info(f"Executing HAProxy setup script in container {vmid} on node {node}")
            self.proxmox.execute_script_in_lxc(node, vmid, setup_script)
            logger.info(f"HAProxy setup script executed successfully in container {vmid}")
        except Exception as e:
            logger.error(f"Failed to execute HAProxy setup script in container {vmid}: {str(e)}")
        
        # Generate and return load balancer configuration
        lb_config = {
            'node': node,
            'id': vmid,
            'hostname': hostname,
            'access_url': f"http://{hostname}",
            'services': {}
        }
        
        # Add service endpoints
        for service_type, instances in service_groups.items():
            if instances:
                default_port = self.get_default_port(service_type)
                lb_config['services'][service_type] = {
                    'hostname': f"{service_type}.{self.domain}",
                    'access_url': f"http://{service_type}.{self.domain}:{default_port}",
                    'instances': [{'hostname': i.get('hostname'), 'node': i.get('node')} for i in instances]
                }
        
        logger.info(f"Load balancer deployment complete: VMID {vmid} on {node}")
        return lb_config
    
    def get_default_port(self, service_type):
        """Get default port for a service type."""
        ports = {
            'qdrant': 6333,
            'ollama': 11434,
            'n8n': 5678,
            'redis': 6379,
            'postgres': 5432
        }
        return ports.get(service_type, 80)
    
    def generate_haproxy_config(self, service_groups):
        """Generate HAProxy configuration for services."""
        stats_password_encrypted = "password_here"  # In real impl, use HAProxy password encryption
        
        config = f"""
global
    log /dev/log    local0
    log /dev/log    local1 notice
    chroot /var/lib/haproxy
    stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners
    stats timeout 30s
    user haproxy
    group haproxy
    daemon

    # Default SSL material locations
    ca-base /etc/ssl/certs
    crt-base /etc/ssl/private

    # SSL configuration
    ssl-default-bind-ciphersuites TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256
    ssl-default-bind-options ssl-min-ver TLSv1.2 no-tls-tickets

defaults
    log     global
    mode    http
    option  httplog
    option  dontlognull
    timeout connect 5000
    timeout client  50000
    timeout server  50000
    errorfile 400 /etc/haproxy/errors/400.http
    errorfile 403 /etc/haproxy/errors/403.http
    errorfile 408 /etc/haproxy/errors/408.http
    errorfile 500 /etc/haproxy/errors/500.http
    errorfile 502 /etc/haproxy/errors/502.http
    errorfile 503 /etc/haproxy/errors/503.http
    errorfile 504 /etc/haproxy/errors/504.http

# HAProxy statistics
listen stats
    bind *:8404
    stats enable
    stats uri /stats
    stats refresh 10s
    stats auth admin:{stats_password_encrypted}
    stats show-legends

"""
        
        # Add frontend and backend configurations for each service group
        for service_type, instances in service_groups.items():
            if not instances:
                continue
            
            default_port = self.get_default_port(service_type)
            
            # Add frontend
            config += f"""
# {service_type.upper()} Service
frontend {service_type}_frontend
    bind *:{default_port}
    default_backend {service_type}_backend
    mode http
    option httplog

"""
            
            # Add backend
            config += f"""
backend {service_type}_backend
    mode http
    balance roundrobin
    option httpchk GET /
    http-check expect status 200
"""
            
            # Add server entries for each instance
            for i, instance in enumerate(instances):
                hostname = instance.get('hostname', f"{service_type}-unknown-{i}")
                config += f"    server {service_type}-{i} {hostname}:{default_port} check\n"
            
            config += "\n"
        
        return config
