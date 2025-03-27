#!/bin/bash
# VM-based Deployment Script for San-O1 AI Infrastructure
# To be executed manually on each Proxmox node
# Creates VMs with GPU integration instead of containers and sets up HA load balancing

set -e  # Exit on error
set -u  # Exit on undefined variable

# ======= CONFIGURATION =======
# Change these variables to match your environment
# You can also export them before running the script

# Node configuration
NODE=${NODE:-"$(hostname)"}
PROXMOX_API_URL=${PROXMOX_API_URL:-"https://localhost:8006/api2/json"}
PROXMOX_USER=${PROXMOX_USER:-"root@pam"}
PROXMOX_PASSWORD=${PROXMOX_PASSWORD:-"your-password"}

# Network configuration
BRIDGE=${BRIDGE:-"vmbr0"}
IP_BASE=${IP_BASE:-"192.168.1"}
DOMAIN=${DOMAIN:-"pve.home"}
GATEWAY=${GATEWAY:-"192.168.0.1"}
NETMASK=${NETMASK:-"255.255.255.0"}

# Storage configuration
VM_STORAGE=${VM_STORAGE:-"HDD"}
ISO_STORAGE=${ISO_STORAGE:-"local"}
UBUNTU_ISO=${UBUNTU_ISO:-"local:iso/ubuntu-24.04.2-live-server-amd64.iso"}

# VM base IDs 
VM_ID_BASE=${VM_ID_BASE:-1000}
LB_VM_ID=${LB_VM_ID:-2000}

# Service resource allocations (can be overridden using environment variables)
QDRANT_CORES=${QDRANT_CORES:-4}
QDRANT_MEMORY=${QDRANT_MEMORY:-8192}
QDRANT_DISK=${QDRANT_DISK:-50}

OLLAMA_CORES=${OLLAMA_CORES:-8}
OLLAMA_MEMORY=${OLLAMA_MEMORY:-32768}
OLLAMA_DISK=${OLLAMA_DISK:-100}

N8N_CORES=${N8N_CORES:-2}
N8N_MEMORY=${N8N_MEMORY:-2048}
N8N_DISK=${N8N_DISK:-20}

REDIS_CORES=${REDIS_CORES:-2}
REDIS_MEMORY=${REDIS_MEMORY:-4096}
REDIS_DISK=${REDIS_DISK:-10}

POSTGRES_CORES=${POSTGRES_CORES:-2}
POSTGRES_MEMORY=${POSTGRES_MEMORY:-4096}
POSTGRES_DISK=${POSTGRES_DISK:-20}

LB_CORES=${LB_CORES:-2}
LB_MEMORY=${LB_MEMORY:-4096}
LB_DISK=${LB_DISK:-20}

# HA configuration
HA_ENABLED=${HA_ENABLED:-false}
HA_GROUP=${HA_GROUP:-"ha_group"}

# ======= FUNCTIONS =======

# Function to authenticate with Proxmox API
proxmox_auth() {
    echo "Authenticating with Proxmox API..."
    local response
    response=$(curl -s -k -d "username=$PROXMOX_USER&password=$PROXMOX_PASSWORD" \
               "$PROXMOX_API_URL/access/ticket")
    
    # Extract ticket and CSRF token
    TICKET=$(echo "$response" | grep -Po '"ticket":"\K[^"]*')
    CSRF_TOKEN=$(echo "$response" | grep -Po '"CSRFPreventionToken":"\K[^"]*')
    
    if [ -z "$TICKET" ] || [ -z "$CSRF_TOKEN" ]; then
        echo "Failed to authenticate with Proxmox API"
        exit 1
    fi
    
    echo "Authentication successful."
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
create_vm() {
    local vmid=$1
    local name=$2
    local cores=$3
    local memory=$4
    local disk=$5
    local ip=$6
    local use_gpu=${7:-false}
    
    echo "Creating VM: $name (ID: $vmid)"
    
    # Basic VM creation with all parameters upfront - using q35 machine type for better passthrough support
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "vmid=$vmid&name=$name&cores=$cores&memory=$memory&ostype=l26&net0=virtio,bridge=$BRIDGE&machine=q35" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu"
    
    # Wait a moment for VM to be created
    sleep 2
    
    # Add disk directly to VM config
    echo "Adding disk to VM $vmid..."
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X PUT \
         -d "scsi0=$VM_STORAGE:${disk},format=raw" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
    
    # Wait for disk to be configured
    sleep 2
    
    # Configure boot order
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X PUT \
         -d "boot=order=scsi0;net0" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
    
    # Add CD-ROM with ISO directly in the config
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X PUT \
         -d "cdrom=$UBUNTU_ISO" \
         "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
    
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
    
    # Configure GPU passthrough if needed
    if [ "$use_gpu" = true ]; then
        echo "Setting up GPU passthrough for VM: $name"
        
        # Get available GPUs (simplified, in production you'd want a more robust way to detect GPUs)
        local gpu_ids
        gpu_ids=$(lspci | grep -i 'vga\|nvidia\|amd' | cut -d' ' -f1)
        
        # If we found a GPU, pass it through
        if [ -n "$gpu_ids" ]; then
            # Get first GPU in the list
            local gpu_id
            gpu_id=$(echo "$gpu_ids" | head -1)
            
            # Enable IOMMU if not already enabled
            # Check different locations for kernel parameters depending on Proxmox version
            GRUB_CONFIG="/etc/default/grub"
            if [ -f "$GRUB_CONFIG" ]; then
                echo "Checking GRUB configuration for IOMMU settings"
                if ! grep -q "intel_iommu=on" "$GRUB_CONFIG"; then
                    echo "Enabling IOMMU in GRUB (requires reboot after script)"
                    # Backup original config
                    cp "$GRUB_CONFIG" "${GRUB_CONFIG}.bak"
                    
                    # Add IOMMU parameters to GRUB
                    sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt /' "$GRUB_CONFIG"
                    
                    # Update GRUB
                    update-grub
                    
                    # Update initramfs
                    update-initramfs -u -k all
                    echo "IOMMU enabled. You will need to reboot the host system!"
                else
                    echo "IOMMU is already enabled in GRUB configuration"
                fi
            else
                echo "GRUB configuration file not found at $GRUB_CONFIG"
                echo "Please manually enable IOMMU for your system"
            fi
            
            echo "Found GPU with ID: $gpu_id"
            
            # Get vendor and device IDs for more precise configuration
            local vendor_device
            vendor_device=$(lspci -n -s $gpu_id | awk '{print $3}')
            echo "GPU Vendor:Device ID: $vendor_device"
            
            # Add PCI passthrough with proper ROM-BAR and PCIe options
            curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
                 -X PUT \
                 -d "hostpci0=$gpu_id,pcie=1,rombar=1,x-vga=1" \
                 "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
            
            # Enable BIOS settings optimal for GPU passthrough
            curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
                 -X PUT \
                 -d "bios=ovmf" \
                 "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config"
        else
            echo "WARNING: No GPU found for passthrough"
        fi
    fi
    
    echo "VM $name created successfully with ID $vmid"
}

# Function to enable HA for a VM
enable_ha() {
    local vmid=$1
    local group=$2
    
    echo "Enabling HA for VM $vmid in group $group"
    
    # Create HA group if it doesn't exist
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "group=$group&nodes=$NODE" \
         "$PROXMOX_API_URL/cluster/ha/groups"
    
    # Add VM to HA
    curl -s -k -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF_TOKEN" \
         -X POST \
         -d "vmid=$vmid&group=$group&state=started" \
         "$PROXMOX_API_URL/cluster/ha/resources"
    
    echo "HA enabled for VM $vmid"
}

# Generate cloud-init setup script for load balancer
generate_lb_setup() {
    local vmid=$1
    local services=$2
    
    cat > /tmp/lb-setup-$vmid.sh <<EOF
#!/bin/bash
set -e

# Update and install HAProxy
apt-get update
apt-get install -y haproxy

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
$services

# Enable HAProxy
systemctl enable haproxy
systemctl restart haproxy

echo "HAProxy Load Balancer configuration complete!"
EOF
    chmod +x /tmp/lb-setup-$vmid.sh
    
    # In a real scenario, you'd transfer this file to the VM after creation
    echo "Created load balancer setup script /tmp/lb-setup-$vmid.sh"
}

# ======= MAIN EXECUTION =======

echo "=== Starting VM-based San-O1 Deployment on $NODE ==="
echo "This script will set up VMs for AI services with GPU integration"
echo "and configure a load balancer VM with HA support."

# Authenticate with Proxmox API
proxmox_auth

# Determine which services to deploy on this node
echo "Please select which services to deploy on this node $NODE:"
echo "1) All services (Ollama, Qdrant, N8N, Redis, Postgres)"
echo "2) Ollama (GPU-accelerated LLM service)"
echo "3) Qdrant (Vector database)"
echo "4) N8N (Workflow automation)"
echo "5) Redis (In-memory database)"
echo "6) PostgreSQL (Relational database)"
echo "7) Load Balancer only"
read -p "Enter your choice (1-7): " SERVICE_CHOICE

# Initialize services to deploy
DEPLOY_OLLAMA=false
DEPLOY_QDRANT=false
DEPLOY_N8N=false
DEPLOY_REDIS=false
DEPLOY_POSTGRES=false
DEPLOY_LB=false

case $SERVICE_CHOICE in
    1)
        DEPLOY_OLLAMA=true
        DEPLOY_QDRANT=true
        DEPLOY_N8N=true
        DEPLOY_REDIS=true
        DEPLOY_POSTGRES=true
        ;;
    2)
        DEPLOY_OLLAMA=true
        ;;
    3)
        DEPLOY_QDRANT=true
        ;;
    4)
        DEPLOY_N8N=true
        ;;
    5)
        DEPLOY_REDIS=true
        ;;
    6)
        DEPLOY_POSTGRES=true
        ;;
    7)
        DEPLOY_LB=true
        ;;
    *)
        echo "Invalid choice. Exiting."
        exit 1
        ;;
esac

# Ask if load balancer should be deployed
if [ "$DEPLOY_LB" = false ]; then
    read -p "Do you want to deploy the load balancer on this node? (y/n): " LB_CHOICE
    if [[ "$LB_CHOICE" =~ ^[Yy] ]]; then
        DEPLOY_LB=true
    fi
fi

# Ask if HA should be enabled for the load balancer
if [ "$DEPLOY_LB" = true ]; then
    read -p "Do you want to enable HA for the load balancer? (y/n): " HA_CHOICE
    if [[ "$HA_CHOICE" =~ ^[Yy] ]]; then
        HA_ENABLED=true
    fi
fi

# Service deployments
SERVICE_VMS=()
LB_CONFIG_SERVICES=""

# Deploy Ollama VM (with GPU)
if [ "$DEPLOY_OLLAMA" = true ]; then
    echo "=== Deploying Ollama VM with GPU passthrough ==="
    
    # Get next VM ID
    OLLAMA_VMID=$(get_next_vmid $VM_ID_BASE)
    OLLAMA_IP="${IP_BASE}.$(( OLLAMA_VMID - VM_ID_BASE + 10 ))"
    
    create_vm $OLLAMA_VMID "ollama" $OLLAMA_CORES $OLLAMA_MEMORY $OLLAMA_DISK $OLLAMA_IP true
    
    # Add to list of deployed services
    SERVICE_VMS+=("ollama:$OLLAMA_VMID:$OLLAMA_IP")
    
    # Add to load balancer config
    LB_CONFIG_SERVICES+="cat >> /etc/haproxy/haproxy.cfg << EOL

# Ollama service
frontend ollama_frontend
    bind *:11434
    default_backend ollama_backend
    mode http
    option httplog

backend ollama_backend
    mode http
    balance roundrobin
    option httpchk GET /
    server ollama-server $OLLAMA_IP:11434 check

EOL
"
fi

# Deploy Qdrant VM
if [ "$DEPLOY_QDRANT" = true ]; then
    echo "=== Deploying Qdrant VM ==="
    
    # Get next VM ID
    QDRANT_VMID=$(get_next_vmid $VM_ID_BASE)
    QDRANT_IP="${IP_BASE}.$(( QDRANT_VMID - VM_ID_BASE + 10 ))"
    
    create_vm $QDRANT_VMID "qdrant" $QDRANT_CORES $QDRANT_MEMORY $QDRANT_DISK $QDRANT_IP false
    
    # Add to list of deployed services
    SERVICE_VMS+=("qdrant:$QDRANT_VMID:$QDRANT_IP")
    
    # Add to load balancer config
    LB_CONFIG_SERVICES+="cat >> /etc/haproxy/haproxy.cfg << EOL

# Qdrant service
frontend qdrant_frontend
    bind *:6333
    default_backend qdrant_backend
    mode http
    option httplog

backend qdrant_backend
    mode http
    balance roundrobin
    option httpchk GET /
    server qdrant-server $QDRANT_IP:6333 check

EOL
"
fi

# Deploy N8N VM
if [ "$DEPLOY_N8N" = true ]; then
    echo "=== Deploying N8N VM ==="
    
    # Get next VM ID
    N8N_VMID=$(get_next_vmid $VM_ID_BASE)
    N8N_IP="${IP_BASE}.$(( N8N_VMID - VM_ID_BASE + 10 ))"
    
    create_vm $N8N_VMID "n8n" $N8N_CORES $N8N_MEMORY $N8N_DISK $N8N_IP false
    
    # Add to list of deployed services
    SERVICE_VMS+=("n8n:$N8N_VMID:$N8N_IP")
    
    # Add to load balancer config
    LB_CONFIG_SERVICES+="cat >> /etc/haproxy/haproxy.cfg << EOL

# N8N service
frontend n8n_frontend
    bind *:5678
    default_backend n8n_backend
    mode http
    option httplog

backend n8n_backend
    mode http
    balance roundrobin
    option httpchk GET /
    server n8n-server $N8N_IP:5678 check

EOL
"
fi

# Deploy Redis VM
if [ "$DEPLOY_REDIS" = true ]; then
    echo "=== Deploying Redis VM ==="
    
    # Get next VM ID
    REDIS_VMID=$(get_next_vmid $VM_ID_BASE)
    REDIS_IP="${IP_BASE}.$(( REDIS_VMID - VM_ID_BASE + 10 ))"
    
    create_vm $REDIS_VMID "redis" $REDIS_CORES $REDIS_MEMORY $REDIS_DISK $REDIS_IP false
    
    # Add to list of deployed services
    SERVICE_VMS+=("redis:$REDIS_VMID:$REDIS_IP")
    
    # Add to load balancer config
    LB_CONFIG_SERVICES+="cat >> /etc/haproxy/haproxy.cfg << EOL

# Redis service
frontend redis_frontend
    bind *:6379
    default_backend redis_backend
    mode tcp
    option tcplog

backend redis_backend
    mode tcp
    balance roundrobin
    server redis-server $REDIS_IP:6379 check

EOL
"
fi

# Deploy PostgreSQL VM
if [ "$DEPLOY_POSTGRES" = true ]; then
    echo "=== Deploying PostgreSQL VM ==="
    
    # Get next VM ID
    POSTGRES_VMID=$(get_next_vmid $VM_ID_BASE)
    POSTGRES_IP="${IP_BASE}.$(( POSTGRES_VMID - VM_ID_BASE + 10 ))"
    
    create_vm $POSTGRES_VMID "postgres" $POSTGRES_CORES $POSTGRES_MEMORY $POSTGRES_DISK $POSTGRES_IP false
    
    # Add to list of deployed services
    SERVICE_VMS+=("postgres:$POSTGRES_VMID:$POSTGRES_IP")
    
    # Add to load balancer config
    LB_CONFIG_SERVICES+="cat >> /etc/haproxy/haproxy.cfg << EOL

# PostgreSQL service
frontend postgres_frontend
    bind *:5432
    default_backend postgres_backend
    mode tcp
    option tcplog

backend postgres_backend
    mode tcp
    balance roundrobin
    server postgres-server $POSTGRES_IP:5432 check

EOL
"
fi

# Deploy Load Balancer VM
if [ "$DEPLOY_LB" = true ]; then
    echo "=== Deploying Load Balancer VM ==="
    
    # Get next VM ID or use specified LB VM ID
    LB_VMID=$(get_next_vmid $LB_VM_ID)
    LB_IP="${IP_BASE}.$(( LB_VMID - VM_ID_BASE + 100 ))"
    
    create_vm $LB_VMID "loadbalancer" $LB_CORES $LB_MEMORY $LB_DISK $LB_IP false
    
    # Generate HAProxy setup script
    generate_lb_setup $LB_VMID "$LB_CONFIG_SERVICES"
    
    # Enable HA if requested
    if [ "$HA_ENABLED" = true ]; then
        enable_ha $LB_VMID $HA_GROUP
    fi
    
    echo "Load balancer VM created with ID $LB_VMID at IP $LB_IP"
    echo "HAProxy setup script generated at /tmp/lb-setup-$LB_VMID.sh"
    echo "You need to transfer this script to the VM and execute it after the VM is running."
fi

# Summary
echo ""
echo "=== Deployment Summary ==="
echo "Node: $NODE"

if [ ${#SERVICE_VMS[@]} -gt 0 ]; then
    echo "Services deployed:"
    for svc in "${SERVICE_VMS[@]}"; do
        IFS=':' read -r name vmid ip <<< "$svc"
        echo "- $name: VM ID $vmid, IP $ip"
    done
else
    echo "No service VMs were deployed."
fi

if [ "$DEPLOY_LB" = true ]; then
    echo "Load Balancer: VM ID $LB_VMID, IP $LB_IP"
    if [ "$HA_ENABLED" = true ]; then
        echo "HA enabled with group $HA_GROUP"
    fi
fi

echo ""
echo "=== Next Steps ==="
echo "1. Once the VMs are created, start them manually in the Proxmox UI"
echo "2. Complete the Ubuntu installation on each VM"
echo "3. For the load balancer VM:"
echo "   a. Transfer the script /tmp/lb-setup-$LB_VMID.sh to the VM"
echo "   b. Run the script to configure HAProxy"
echo "4. For service VMs, install and configure the respective services"
echo "   (Ollama, Qdrant, etc.) according to your requirements"
echo ""
echo "Note: This script creates VMs with the Ubuntu Server ISO mounted."
echo "You can also create custom templates with your services pre-installed"
echo "for faster deployment in the future."
