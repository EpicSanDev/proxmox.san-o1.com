#!/bin/bash
# High Availability Load Balancer Setup for San-O1 VM Infrastructure
# This script configures a load balancer VM with HA support in Proxmox

set -e  # Exit on error
set -u  # Exit on undefined variable

# ======= CONFIGURATION =======
# Change these variables to match your environment
# You can also export them before running the script

# Proxmox configuration
NODE=${NODE:-"$(hostname)"}
PROXMOX_API_URL=${PROXMOX_API_URL:-"https://localhost:8006/api2/json"}
PROXMOX_USER=${PROXMOX_USER:-"root@pam"}
PROXMOX_PASSWORD=${PROXMOX_PASSWORD:-"your-password"}

# Network configuration
BRIDGE=${BRIDGE:-"vmbr0"}
IP_BASE=${IP_BASE:-"192.168.1"}
DOMAIN=${DOMAIN:-"ai-cluster.local"}
GATEWAY=${GATEWAY:-"192.168.1.1"}
NETMASK=${NETMASK:-"255.255.255.0"}

# Storage configuration
VM_STORAGE=${VM_STORAGE:-"local-lvm"}
ISO_STORAGE=${ISO_STORAGE:-"local"}
UBUNTU_ISO=${UBUNTU_ISO:-"local:iso/ubuntu-22.04-live-server-amd64.iso"}

# Load Balancer VM configuration
LB_VM_ID=${LB_VM_ID:-2000}
LB_CORES=${LB_CORES:-2}
LB_MEMORY=${LB_MEMORY:-4096}
LB_DISK=${LB_DISK:-20}

# HA configuration
HA_ENABLED=${HA_ENABLED:-true}
HA_GROUP=${HA_GROUP:-"ha_group"}

# Service configuration - add the IPs of your service VMs here
OLLAMA_IP=${OLLAMA_IP:-""}
QDRANT_IP=${QDRANT_IP:-""}
N8N_IP=${N8N_IP:-""}
REDIS_IP=${REDIS_IP:-""}
POSTGRES_IP=${POSTGRES_IP:-""}

# Service ports
OLLAMA_PORT=${OLLAMA_PORT:-11434}
QDRANT_PORT=${QDRANT_PORT:-6333}
N8N_PORT=${N8N_PORT:-5678}
REDIS_PORT=${REDIS_PORT:-6379}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

# ======= FUNCTIONS =======

# Display a message with timestamp
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Function to authenticate with Proxmox API
proxmox_auth() {
    log_message "Authenticating with Proxmox API..."
    local response
    response=$(curl -s -k -d "username=$PROXMOX_USER&password=$PROXMOX_PASSWORD" \
               "$PROXMOX_API_URL/access/ticket")
    
    # Extract ticket and CSRF token
    TICKET=$(echo "$response" | grep -Po '"ticket":"\K[^"]*')
    CSRF_TOKEN=$(echo "$response" | grep -Po '"CSRFPreventionToken":"\K[^"]*')
    
    if [ -z "$TICKET" ] || [ -z "$CSRF_TOKEN" ]; then
        log_message "Failed to authenticate with Proxmox API"
        exit 1
    fi
    
    log_message "Authentication successful."
}

# Function to check if a VM with given ID exists
vm_exists() {
    local vmid=$1
    local status_code
    
    status_code=$(curl -s -k -w "%{http_code}" -o /dev/null \
                 -b "PVEAuthCookie=$TICKET" \
                 "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/status/current")
    
    if [ "$status_code" -eq 200 ]; then
        return 0  # VM exists
    else
        return 1  # VM does not exist
    fi
}

# Function to get next available VM ID
get_next_vmid() {
    local base_id=$1
    local vmid=$base_id
    
    while vm_exists $vmid; do
        vmid=$((vmid + 1))
    done
    
    echo $vmid
}

# Function to create a VM
create_lb_vm() {
    local vmid=$1
    local name=$2
    local cores=$3
    local memory=$4
    local disk=$5
    local ip=$6
    
    log_message "Creating Load Balancer VM: $name (ID: $vmid)"
    
    # Basic VM creation with all parameters upfront
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "vmid=$vmid&name=$name&cores=$cores&memory=$memory&ostype=l26&net0=model=virtio,bridge=$BRIDGE" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu"
    
    # Wait a moment for VM to be created
    sleep 2
    
    # Add disk
    disk_response=$(curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "storage=$VM_STORAGE&size=${disk}G&format=raw&vmid=$vmid" \
         "$PROXMOX_API_URL/nodes/$NODE/storage/$VM_STORAGE/content")
    
    log_message "Disk creation response: $disk_response"
    
    # Wait for disk to be created
    sleep 2
    
    # Configure boot order
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X PUT \
         -d "boot=c" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
    
    # Add CD-ROM with ISO
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "idlist=ide2&vmid=$vmid" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/hardware"
    
    sleep 1
    
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X PUT \
         -d "media=cdrom&file=$UBUNTU_ISO" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config/ide2"
    
    # Configure cloud-init options
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X PUT \
         -d "ciuser=root&cipassword=san-o1-password&nameserver=8.8.8.8&searchdomain=$DOMAIN" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
    
    # Configure IP
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X PUT \
         -d "ipconfig0=ip=$ip/24,gw=$GATEWAY" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
    
    log_message "Load Balancer VM $name created successfully with ID $vmid"
}

# Function to enable HA for a VM
enable_ha() {
    local vmid=$1
    local group=$2
    
    log_message "Enabling HA for Load Balancer VM $vmid in group $group"
    
    # Create HA group if it doesn't exist
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "group=$group&nodes=$NODE" \
         "$PROXMOX_API_URL/cluster/ha/groups" || true
    
    # Add VM to HA
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "vmid=$vmid&group=$group&state=started" \
         "$PROXMOX_API_URL/cluster/ha/resources"
    
    log_message "HA enabled for VM $vmid"
}

# Generate HAProxy configuration for the load balancer
generate_haproxy_config() {
    local vmid=$1
    local config_services=""
    
    # Add Ollama configuration if IP is provided
    if [ -n "$OLLAMA_IP" ]; then
        config_services+="cat >> /etc/haproxy/haproxy.cfg << EOL

# Ollama service
frontend ollama_frontend
    bind *:$OLLAMA_PORT
    default_backend ollama_backend
    mode http
    option httplog

backend ollama_backend
    mode http
    balance roundrobin
    option httpchk GET /
    server ollama-server $OLLAMA_IP:$OLLAMA_PORT check

EOL
"
    fi
    
    # Add Qdrant configuration if IP is provided
    if [ -n "$QDRANT_IP" ]; then
        config_services+="cat >> /etc/haproxy/haproxy.cfg << EOL

# Qdrant service
frontend qdrant_frontend
    bind *:$QDRANT_PORT
    default_backend qdrant_backend
    mode http
    option httplog

backend qdrant_backend
    mode http
    balance roundrobin
    option httpchk GET /
    server qdrant-server $QDRANT_IP:$QDRANT_PORT check

EOL
"
    fi
    
    # Add N8N configuration if IP is provided
    if [ -n "$N8N_IP" ]; then
        config_services+="cat >> /etc/haproxy/haproxy.cfg << EOL

# N8N service
frontend n8n_frontend
    bind *:$N8N_PORT
    default_backend n8n_backend
    mode http
    option httplog

backend n8n_backend
    mode http
    balance roundrobin
    option httpchk GET /
    server n8n-server $N8N_IP:$N8N_PORT check

EOL
"
    fi
    
    # Add Redis configuration if IP is provided
    if [ -n "$REDIS_IP" ]; then
        config_services+="cat >> /etc/haproxy/haproxy.cfg << EOL

# Redis service
frontend redis_frontend
    bind *:$REDIS_PORT
    default_backend redis_backend
    mode tcp
    option tcplog

backend redis_backend
    mode tcp
    balance roundrobin
    server redis-server $REDIS_IP:$REDIS_PORT check

EOL
"
    fi
    
    # Add PostgreSQL configuration if IP is provided
    if [ -n "$POSTGRES_IP" ]; then
        config_services+="cat >> /etc/haproxy/haproxy.cfg << EOL

# PostgreSQL service
frontend postgres_frontend
    bind *:$POSTGRES_PORT
    default_backend postgres_backend
    mode tcp
    option tcplog

backend postgres_backend
    mode tcp
    balance roundrobin
    server postgres-server $POSTGRES_IP:$POSTGRES_PORT check

EOL
"
    fi
    
    # Generate the complete HAProxy setup script
    cat > /tmp/ha-lb-setup-$vmid.sh <<EOF
#!/bin/bash
set -e

# Update and install HAProxy
apt-get update
apt-get install -y haproxy keepalived

# Create HAProxy configuration
cat > /etc/haproxy/haproxy.cfg <<'EOL'
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
    stats auth admin:san-o1-admin
    stats show-legends

# Service Backends
EOL

# Add services
$config_services

# Configure Keepalived for HA
cat > /etc/keepalived/keepalived.conf <<EOL
vrrp_script chk_haproxy {
    script "killall -0 haproxy"
    interval 2
    weight 2
}

vrrp_instance VI_1 {
    state MASTER
    interface eth0
    virtual_router_id 51
    priority 100
    advert_int 1
    authentication {
        auth_type PASS
        auth_pass san-o1-secret
    }
    virtual_ipaddress {
        ${IP_BASE}.250/24
    }
    track_script {
        chk_haproxy
    }
}
EOL

# Enable services
systemctl enable haproxy
systemctl restart haproxy
systemctl enable keepalived
systemctl restart keepalived

echo "HAProxy and Keepalived Load Balancer configuration complete!"
EOF
    chmod +x /tmp/ha-lb-setup-$vmid.sh
    
    log_message "Created load balancer setup script at /tmp/ha-lb-setup-$vmid.sh"
}

# ======= MAIN EXECUTION =======

log_message "=== Starting HA Load Balancer Setup for San-O1 ==="
log_message "This script will setup a high-availability load balancer VM for your San-O1 infrastructure."

# Authenticate with Proxmox API
proxmox_auth

# Get VM ID
LB_VMID=$(get_next_vmid $LB_VM_ID)
LB_IP="${IP_BASE}.$(( LB_VMID - LB_VM_ID + 200 ))"

# Create the load balancer VM
create_lb_vm $LB_VMID "ha-loadbalancer" $LB_CORES $LB_MEMORY $LB_DISK $LB_IP

# Generate HAProxy and Keepalived configuration
generate_haproxy_config $LB_VMID

# Enable HA if required
if [ "$HA_ENABLED" = true ]; then
    enable_ha $LB_VMID $HA_GROUP
fi

# Check if there are valid service IPs
has_services=false
for service_ip in "$OLLAMA_IP" "$QDRANT_IP" "$N8N_IP" "$REDIS_IP" "$POSTGRES_IP"; do
    if [ -n "$service_ip" ]; then
        has_services=true
        break
    fi
done

# Summary
log_message ""
log_message "=== Load Balancer Setup Summary ==="
log_message "Node: $NODE"
log_message "Load Balancer: VM ID $LB_VMID, IP $LB_IP"
log_message "Virtual IP (For HA): ${IP_BASE}.250"

if [ "$HA_ENABLED" = true ]; then
    log_message "HA enabled with group $HA_GROUP"
fi

if [ "$has_services" = true ]; then
    log_message "Services configured:"
    [ -n "$OLLAMA_IP" ] && log_message "- Ollama: $OLLAMA_IP:$OLLAMA_PORT"
    [ -n "$QDRANT_IP" ] && log_message "- Qdrant: $QDRANT_IP:$QDRANT_PORT"
    [ -n "$N8N_IP" ] && log_message "- N8N: $N8N_IP:$N8N_PORT"
    [ -n "$REDIS_IP" ] && log_message "- Redis: $REDIS_IP:$REDIS_PORT"
    [ -n "$POSTGRES_IP" ] && log_message "- PostgreSQL: $POSTGRES_IP:$POSTGRES_PORT"
else
    log_message "No service IPs were provided. You'll need to update the HAProxy configuration manually."
fi

log_message ""
log_message "=== Next Steps ==="
log_message "1. Start the VM manually in the Proxmox UI or via API"
log_message "2. Complete the Ubuntu installation on the VM"
log_message "3. Copy the HAProxy and Keepalived setup script to the VM:"
log_message "   scp /tmp/ha-lb-setup-$LB_VMID.sh root@$LB_IP:/tmp/"
log_message "4. Connect to the VM and run the setup script:"
log_message "   ssh root@$LB_IP 'bash /tmp/ha-lb-setup-$LB_VMID.sh'"

log_message ""
log_message "=== To Add More Backend Services ==="
log_message "Edit /tmp/ha-lb-setup-$LB_VMID.sh and add your additional services,"
log_message "then transfer and execute the script again."
log_message ""
log_message "For HA setup with multiple nodes, run this script on each node in your Proxmox cluster,"
log_message "and make sure to configure Keepalived with the proper priorities."
