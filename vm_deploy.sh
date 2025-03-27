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
PROXMOX_PASSWORD=${PROXMOX_PASSWORD:-"your-password"} # *** IMPORTANT: Replace or use environment variable ***

# Network configuration
BRIDGE=${BRIDGE:-"vmbr0"}
IP_BASE=${IP_BASE:-"192.168.1"}
DOMAIN=${DOMAIN:-"pve.home"}
GATEWAY=${GATEWAY:-"192.168.0.1"} # Ensure this is correct for your network
NETMASK=${NETMASK:-"255.255.255.0"} # Note: CIDR notation (/24) is used for ipconfig0

# Storage configuration
VM_STORAGE=${VM_STORAGE:-"HDD"}
ISO_STORAGE=${ISO_STORAGE:-"local"}
UBUNTU_ISO=${UBUNTU_ISO:-"local:iso/ubuntu-24.04.2-live-server-amd64.iso"} # Ensure this ISO exists on the specified storage

# VM base IDs
VM_ID_BASE=${VM_ID_BASE:-1000}
LB_VM_ID_BASE=${LB_VM_ID_BASE:-2000} # Renamed to avoid confusion with specific ID

# Service resource allocations (can be overridden using environment variables)
QDRANT_CORES=${QDRANT_CORES:-4}
QDRANT_MEMORY=${QDRANT_MEMORY:-8192}
QDRANT_DISK=${QDRANT_DISK:-50} # Disk size in GB

OLLAMA_CORES=${OLLAMA_CORES:-8}
OLLAMA_MEMORY=${OLLAMA_MEMORY:-32768}
OLLAMA_DISK=${OLLAMA_DISK:-100} # Disk size in GB

N8N_CORES=${N8N_CORES:-2}
N8N_MEMORY=${N8N_MEMORY:-2048}
N8N_DISK=${N8N_DISK:-20} # Disk size in GB

REDIS_CORES=${REDIS_CORES:-2}
REDIS_MEMORY=${REDIS_MEMORY:-4096}
REDIS_DISK=${REDIS_DISK:-10} # Disk size in GB

POSTGRES_CORES=${POSTGRES_CORES:-2}
POSTGRES_MEMORY=${POSTGRES_MEMORY:-4096}
POSTGRES_DISK=${POSTGRES_DISK:-20} # Disk size in GB

LB_CORES=${LB_CORES:-2}
LB_MEMORY=${LB_MEMORY:-4096}
LB_DISK=${LB_DISK:-20} # Disk size in GB

# HA configuration
HA_ENABLED=${HA_ENABLED:-false}
HA_GROUP=${HA_GROUP:-"ha_group"}

# Global variables for auth tokens
TICKET=""
CSRF_TOKEN=""

# ======= FUNCTIONS =======

# Function to authenticate with Proxmox API
proxmox_auth() {
    echo "Authenticating with Proxmox API..."
    local response
    # Use insecure (-k) for self-signed certs, remove if using valid certs
    response=$(curl -s -k -d "username=$PROXMOX_USER&password=$PROXMOX_PASSWORD" \
               "$PROXMOX_API_URL/access/ticket")

    TICKET=$(echo "$response" | grep -Po '"ticket":"\K[^"]*')
    CSRF_TOKEN=$(echo "$response" | grep -Po '"CSRFPreventionToken":"\K[^"]*')

    if [ -z "$TICKET" ] || [ -z "$CSRF_TOKEN" ]; then
        echo "Failed to authenticate with Proxmox API. Check credentials and API URL."
        echo "Response: $response"
        exit 1
    fi

    # Set cookie for subsequent requests
    PVE_COOKIE="PVEAuthCookie=$TICKET"

    echo "Authentication successful."
}

# Function to check if a VM with given ID exists
vm_exists() {
    local vmid=$1
    local status_code

    # Make curl timeout after a reasonable period (e.g., 10 seconds)
    status_code=$(curl --connect-timeout 10 -s -k -w "%{http_code}" -o /dev/null \
                 -b "$PVE_COOKIE" \
                 "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/status/current")

    if [ "$status_code" -eq 200 ]; then
        return 0  # VM exists
    elif [ "$status_code" -eq 404 ]; then
        return 1  # VM does not exist
    else
        # Redirect this warning message to stderr
        echo "Warning: vm_exists check for VM $vmid received unexpected HTTP status: $status_code" >&2
        # Optionally add more details about the curl command or response here if needed for debugging
        return 1 # Assume it doesn't exist or there's an issue, allows get_next_vmid to continue
    fi
}

# Function to get next available VM ID
get_next_vmid() {
    local base_id=$1
    local vmid=$base_id

    # Print informational messages to standard error (stderr)
    echo "Finding next available VM ID starting from $base_id..." >&2
    while vm_exists $vmid; do
        echo "VM ID $vmid is taken, trying next..." >&2
        vmid=$((vmid + 1))
    done

    echo "Using VM ID $vmid" >&2
    # Only echo the final result (the VM ID) to standard output (stdout)
    echo $vmid
}

# Function to make an API call with error checking
api_call() {
    local method=$1
    local url=$2
    shift 2
    local data=("$@") # Remaining arguments are data pairs like key=value

    local response
    local http_code

    echo "API Call: $method $url Data: ${data[*]}"

    # Construct -d options
    local curl_data=()
    for item in "${data[@]}"; do
        curl_data+=("-d")
        curl_data+=("$item")
    done

    response=$(curl -s -k -w "\nHTTP_CODE:%{http_code}" \
                 -b "$PVE_COOKIE" -H "CSRFPreventionToken: $CSRF_TOKEN" \
                 -X "$method" \
                 "${curl_data[@]}" \
                 "$url")

    # Extract HTTP code and response body
    http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d':' -f2)
    response_body=$(echo "$response" | sed '$d') # Remove last line (HTTP_CODE)

    echo "API Response (Code: $http_code): $response_body"

    # Check for success (2xx codes)
    if [[ "$http_code" =~ ^2 ]]; then
        echo "API call successful."
        return 0
    else
        echo "API call failed!"
        # Try to parse Proxmox error message if available
        local error_msg=$(echo "$response_body" | grep -Po '"message":"\K[^"]*' || echo "No specific message found.")
        local errors=$(echo "$response_body" | grep -Po '"errors":({.*?}|null)' || echo "No specific errors found.")
        echo "Error Message: $error_msg"
        echo "Errors Detail: $errors"
        # Optionally exit on failure, depending on context
        # exit 1
        return 1 # Indicate failure
    fi
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

    echo "--- Creating VM: $name (ID: $vmid) ---"

    # Check if VM already exists
    if vm_exists $vmid; then
        echo "VM ID $vmid already exists. Skipping creation."
        return 1
    fi

    # 1. Create the basic VM shell
    echo "Step 1: Creating VM shell..."
    if ! api_call POST "$PROXMOX_API_URL/nodes/$NODE/qemu" \
        "vmid=$vmid" \
        "name=$name" \
        "cores=$cores" \
        "memory=$memory" \
        "ostype=l26" \
        "machine=q35" \
        "net0=model=virtio,bridge=$BRIDGE" \
        "scsihw=virtio-scsi-pci"; then # Use virtio-scsi controller
        echo "Failed to create VM shell for $name (ID: $vmid). Aborting VM creation."
        return 1
    fi
    sleep 3 # Allow time for VM object creation

    # 2. Add Disk
    echo "Step 2: Adding disk..."
    # Note: size is in GB, Proxmox API expects size with G suffix for qcow2/raw on storage like LVM, ZFS.
    # For directory storage, it might just be the number. Adapt if needed. Assuming G suffix works.
    # Correct API parameter is scsiX where X is the unit number.
    if ! api_call POST "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
        "scsi0=$VM_STORAGE:$disk,format=raw"; then
       echo "Warning: Failed to add disk using POST. Trying PUT..."
        if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
            "scsi0=$VM_STORAGE:$disk,format=raw"; then
            echo "Failed to add disk to VM $vmid. Check storage '$VM_STORAGE' and permissions."
            # Consider cleanup or manual intervention needed
            return 1
        fi
    fi
    sleep 3 # Allow time for disk configuration

    # 3. Add CD-ROM with ISO
    echo "Step 3: Adding CD-ROM..."
    # Using ide2 is common for CD-ROM
    if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
        "ide2=$UBUNTU_ISO,media=cdrom"; then
        echo "Failed to add CD-ROM/ISO to VM $vmid. Check ISO path '$UBUNTU_ISO'."
        return 1
    fi
    sleep 2

    # 4. Configure Boot Order
    echo "Step 4: Setting boot order..."
    # Boot from CD first, then disk
    if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
        "boot=order=ide2;scsi0"; then # Try CD first, then disk
        echo "Failed to set boot order for VM $vmid."
        return 1
    fi
    sleep 2

    # 5. Configure Cloud-Init base settings (user, password, search domain, nameserver)
    echo "Step 5: Configuring Cloud-Init base settings..."
    # Setting password via API might require specific storage setup (cloudinit drive)
    # It's often easier to use SSH keys or configure post-boot.
    # Using a simple password here for demonstration.
    # Add cloud-init drive storage reference if needed, e.g., cicustom="vendor=<storage>:snippets/vendor.yaml"
    if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
        "ciuser=ubuntu" \
        "cipassword=san-o1-password" \
        "searchdomain=$DOMAIN" \
        "nameserver=8.8.8.8"; then # Use your preferred DNS
        echo "Failed to set basic Cloud-Init options for VM $vmid."
        return 1
    fi
    sleep 2

    # 6. Configure Cloud-Init IP Address
    echo "Step 6: Configuring Cloud-Init IP..."
    # Correct format is ip=IP/CIDR,gw=GATEWAY
    if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
        "ipconfig0=ip=$ip/24,gw=$GATEWAY"; then # Assuming /24 subnet from NETMASK
        echo "Failed to set Cloud-Init IP config for VM $vmid."
        return 1
    fi
    sleep 2

    # 7. Configure GPU passthrough if needed
    if [ "$use_gpu" = true ]; then
        echo "Step 7: Setting up GPU passthrough for VM: $name"

        # Enable UEFI BIOS (OVMF) - required for modern passthrough
        echo "Setting BIOS to OVMF..."
        if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
            "bios=ovmf"; then
            echo "Failed to set BIOS to OVMF for VM $vmid."
            # OVMF requires an EFI disk. Add it.
            echo "Adding EFI Disk..."
            # Ensure you have storage configured for EFI disks (e.g., VM_STORAGE or another)
            # Size must be small, format must be raw or qcow2.
             if ! api_call POST "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
                 "efidisk0=$VM_STORAGE:1,format=raw,efitype=4m"; then # Adjust size/format if needed
                echo "Failed to add EFI disk. GPU passthrough might fail."
                # return 1 # Decide if this is critical
            else
                 echo "EFI Disk added. Retrying setting BIOS to OVMF..."
                 sleep 2
                 if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
                    "bios=ovmf"; then
                     echo "Failed to set BIOS to OVMF even after adding EFI disk."
                     return 1
                 fi
            fi
        fi
        sleep 2

        # Simplified GPU detection (find first NVIDIA or AMD VGA controller)
        # In production, identify the specific GPU and associated devices (audio) more robustly
        local gpu_id=""
        gpu_id=$(lspci | grep -Ei 'VGA compatible controller.*(NVIDIA|AMD)' | head -n 1 | cut -d' ' -f1)

        if [ -n "$gpu_id" ]; then
            echo "Attempting to pass through GPU with ID: $gpu_id"

            # Check IOMMU (basic check, assumes already enabled system-wide)
            if ! dmesg | grep -q -e "DMAR: IOMMU enabled" -e "AMD-Vi: Enabling IOMMU"; then
                 echo "WARNING: IOMMU does not appear to be enabled in kernel boot messages."
                 echo "Ensure intel_iommu=on or amd_iommu=on is in your kernel cmdline and IOMMU/VT-d is enabled in BIOS."
                 echo "GPU passthrough might fail."
                 # Consider adding a prompt to continue or exit here
            fi

            # Add PCI passthrough device
            # hostpciX=ID,[OPTIONS]
            # Use pcie=1 for PCIe devices, rombar=1 often needed
            # x-vga=1 if passing through the primary/boot GPU (requires specific host setup)
            # vga=none might be needed if NOT using x-vga=1
            # Simplified: Assuming standard PCIe passthrough
            echo "Adding hostpci0 config..."
            if ! api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" \
                "hostpci0=$gpu_id,pcie=1,rombar=1"; then # Removed x-vga=1 initially, add if needed
                # Add vga=none as another option if the above fails or causes issues
                # api_call PUT "$PROXMOX_API_URL/nodes/$NODE/qemu/$vmid/config" "vga=none"
                echo "Failed to configure PCI passthrough for $gpu_id on VM $vmid."
                return 1
            fi
             echo "GPU passthrough configured for $gpu_id. Ensure host is properly set up (IOMMU, vfio modules)."
        else
            echo "WARNING: No suitable GPU found via lspci for passthrough on host $NODE."
            echo "Skipping GPU configuration for VM $vmid."
        fi
    fi

    echo "--- VM $name (ID: $vmid) configuration complete ---"
    echo "IP Address: $ip"
    echo "Next: Start the VM and complete OS installation."
    return 0
}


# Function to enable HA for a VM
enable_ha() {
    local vmid=$1
    local group=$2

    echo "Enabling HA for VM $vmid in group $group..."

    # Check if HA group exists first (API doesn't have a direct 'check' endpoint easily)
    # We can try to create it; if it exists, the command might fail harmlessly or succeed.
    # The 'nodes' parameter in group creation might overwrite existing nodes, be careful in multi-node setups.
    # Better to ensure group exists manually or use a more complex check.
    # Simple approach: attempt creation, ignore 'already exists' type errors if possible.
    echo "Attempting to create/update HA group '$group'..."
    api_call POST "$PROXMOX_API_URL/cluster/ha/groups" \
        "group=$group" \
        "nodes=$NODE" # This assumes the group should only contain the current node, adjust if needed

    # Add VM to HA resource list
    echo "Adding VM $vmid to HA resources..."
    if ! api_call POST "$PROXMOX_API_URL/cluster/ha/resources" \
        "sid=vm:$vmid" \
        "group=$group" \
        "state=started"; then # Or 'enabled' if you don't want it started automatically by HA initially
       echo "Failed to enable HA for VM $vmid."
       return 1
    fi

    echo "HA enabled for VM $vmid."
    return 0
}

# Generate cloud-init setup script for load balancer
generate_lb_setup() {
    local vmid=$1 # Used for naming the script file
    local services_config=$2 # The generated HAProxy backend config blocks

    local lb_setup_script="/tmp/lb-setup-$vmid.sh"

    echo "Generating HAProxy setup script at $lb_setup_script..."

    # Basic structure of the script to be run inside the LB VM
    cat > "$lb_setup_script" <<EOF
#!/bin/bash
set -e # Exit on error
set -u # Exit on undefined variable

echo "--- Starting HAProxy Configuration ---"

# 1. Update package list and install HAProxy
echo "Updating apt and installing HAProxy..."
export DEBIAN_FRONTEND=noninteractive # Avoid interactive prompts
apt-get update -y
apt-get install -y haproxy

# 2. Backup existing config
echo "Backing up default HAProxy config..."
cp /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.backup.$(date +%F_%T)

# 3. Create new HAProxy configuration file
echo "Creating new HAProxy configuration..."
cat > /etc/haproxy/haproxy.cfg <<'HAPROXY_CONF'
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
    mode    http # Default mode, can be overridden per backend
    option  httplog
    option  dontlognull
    timeout connect 5s # Shorter connect timeout
    timeout client  50s
    timeout server  50s
    errorfile 400 /etc/haproxy/errors/400.http
    errorfile 403 /etc/haproxy/errors/403.http
    errorfile 408 /etc/haproxy/errors/408.http
    errorfile 500 /etc/haproxy/errors/500.http
    errorfile 502 /etc/haproxy/errors/502.http
    errorfile 503 /etc/haproxy/errors/503.http
    errorfile 504 /etc/haproxy/errors/504.http

# HAProxy Statistics Page (optional but useful)
listen stats
    bind *:8404
    stats enable
    stats uri /stats # URL to access stats page
    stats refresh 10s
    # Simple authentication, change credentials in production
    stats auth admin:san-o1-admin
    stats show-legends # Show descriptions
    stats admin if TRUE # Allow basic admin actions from stats page if needed

# --- Service Backends Start ---
HAPROXY_CONF

# 4. Append Service Configurations
echo "Appending service backend configurations..."
# The content of $services_config variable will be written here
cat >> /etc/haproxy/haproxy.cfg <<'EOF_SERVICES'
$services_config
EOF_SERVICES

# --- Service Backends End --- is implicitly after $services_config is appended

# 5. Validate configuration
echo "Validating HAProxy configuration..."
haproxy -c -f /etc/haproxy/haproxy.cfg
if [ $? -ne 0 ]; then
    echo "HAProxy configuration validation failed. Please check /etc/haproxy/haproxy.cfg"
    exit 1
fi

# 6. Enable and restart HAProxy service
echo "Enabling and restarting HAProxy service..."
systemctl enable haproxy
systemctl restart haproxy

echo "--- HAProxy Load Balancer configuration complete! ---"
echo "Access stats (if enabled) at http://<LB_IP>:8404/stats"
EOF

    # Make the script executable
    chmod +x "$lb_setup_script"

    echo "Load balancer setup script generated: $lb_setup_script"
    echo "You will need to copy this script to the load balancer VM (ID $vmid) and run it after the OS is installed."
}


# ======= MAIN EXECUTION =======

echo "=== Starting VM-based San-O1 Deployment on $NODE ==="
echo "This script will set up VMs for AI services with GPU integration"
echo "and optionally configure a load balancer VM with HA support."
echo "*** Ensure Proxmox credentials and config variables are correct! ***"
echo "*** IMPORTANT: GPU Passthrough requires host system reboot after IOMMU kernel parameters are added/changed. ***"
echo "*** This script performs a basic check but doesn't enforce the reboot. ***"

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
read -p "Enter your choice (1-7) [Default: 1]: " SERVICE_CHOICE
SERVICE_CHOICE=${SERVICE_CHOICE:-1} # Default to 1 if empty

# Initialize services to deploy
DEPLOY_OLLAMA=false
DEPLOY_QDRANT=false
DEPLOY_N8N=false
DEPLOY_REDIS=false
DEPLOY_POSTGRES=false
DEPLOY_LB=false

# Use flags for clarity
case $SERVICE_CHOICE in
    1) DEPLOY_OLLAMA=true; DEPLOY_QDRANT=true; DEPLOY_N8N=true; DEPLOY_REDIS=true; DEPLOY_POSTGRES=true ;;
    2) DEPLOY_OLLAMA=true ;;
    3) DEPLOY_QDRANT=true ;;
    4) DEPLOY_N8N=true ;;
    5) DEPLOY_REDIS=true ;;
    6) DEPLOY_POSTGRES=true ;;
    7) DEPLOY_LB=true ;;
    *) echo "Invalid choice '$SERVICE_CHOICE'. Exiting."; exit 1 ;;
esac

# Ask if load balancer should be deployed, unless "LB only" was chosen
if [ "$SERVICE_CHOICE" != "7" ]; then
    read -p "Do you also want to deploy the load balancer VM on this node? (y/n) [Default: n]: " LB_CHOICE
    if [[ "${LB_CHOICE:-n}" =~ ^[Yy]$ ]]; then
        DEPLOY_LB=true
    fi
fi

# Ask if HA should be enabled for the load balancer if it's being deployed
if [ "$DEPLOY_LB" = true ]; then
    read -p "Do you want to enable Proxmox HA for the load balancer VM? (y/n) [Default: n]: " HA_CHOICE
    if [[ "${HA_CHOICE:-n}" =~ ^[Yy]$ ]]; then
        HA_ENABLED=true
    else
        HA_ENABLED=false # Ensure it's false if not 'y'
    fi
fi

# --- Service Deployments ---
SERVICE_VMS=() # Array to store info about deployed service VMs
LB_CONFIG_SERVICES="" # String to build HAProxy config snippets
declare NEXT_VMID # Use declare to ensure scope if needed

# Function to add service backend config for HAProxy
add_lb_service_config() {
    local service_name=$1
    local bind_port=$2
    local backend_ip=$3
    local backend_port=$4
    local mode=${5:-http} # Default to http mode, use tcp for redis/postgres
    local check_opts=${6:-"option httpchk GET /"} # Default health check for http

    if [[ "$mode" == "tcp" ]]; then
        check_opts="check" # Basic TCP check
    fi

    # Append frontend and backend config blocks
    LB_CONFIG_SERVICES+=$(cat <<EOL

# --- $service_name Service ---
frontend ${service_name}_frontend
    bind *:$bind_port
    mode $mode
    default_backend ${service_name}_backend
    $( [[ "$mode" == "http" ]] && echo "option httplog" || echo "option tcplog" )

backend ${service_name}_backend
    mode $mode
    balance roundrobin # Simple load balancing
    # Add server(s) - currently assumes one instance per script run
    server ${service_name}-server1 $backend_ip:$backend_port $check_opts

EOL
)
}


# Deploy Ollama VM (with GPU)
if [ "$DEPLOY_OLLAMA" = true ]; then
    echo ""
    echo "=== Deploying Ollama VM (GPU Recommended) ==="
    NEXT_VMID=$(get_next_vmid $VM_ID_BASE)
    # Calculate IP based on offset from base ID
    OLLAMA_IP="${IP_BASE}.$(( NEXT_VMID % 250 + 5 ))" # Simple IP assignment, adjust range as needed
    if create_vm $NEXT_VMID "ollama-$NODE" $OLLAMA_CORES $OLLAMA_MEMORY $OLLAMA_DISK $OLLAMA_IP true; then
        SERVICE_VMS+=("ollama:$NEXT_VMID:$OLLAMA_IP")
        add_lb_service_config "ollama" 11434 $OLLAMA_IP 11434 "http"
        VM_ID_BASE=$((NEXT_VMID + 1)) # Increment base ID for next VM
    else
        echo "Ollama VM deployment failed."
    fi
fi

# Deploy Qdrant VM
if [ "$DEPLOY_QDRANT" = true ]; then
    echo ""
    echo "=== Deploying Qdrant VM ==="
    NEXT_VMID=$(get_next_vmid $VM_ID_BASE)
    QDRANT_IP="${IP_BASE}.$(( NEXT_VMID % 250 + 5 ))"
    if create_vm $NEXT_VMID "qdrant-$NODE" $QDRANT_CORES $QDRANT_MEMORY $QDRANT_DISK $QDRANT_IP false; then
        SERVICE_VMS+=("qdrant:$NEXT_VMID:$QDRANT_IP")
        add_lb_service_config "qdrant" 6333 $QDRANT_IP 6333 "http" "option httpchk GET /cluster" # Use appropriate health check endpoint
        VM_ID_BASE=$((NEXT_VMID + 1))
    else
        echo "Qdrant VM deployment failed."
    fi
fi

# Deploy N8N VM
if [ "$DEPLOY_N8N" = true ]; then
    echo ""
    echo "=== Deploying N8N VM ==="
    NEXT_VMID=$(get_next_vmid $VM_ID_BASE)
    N8N_IP="${IP_BASE}.$(( NEXT_VMID % 250 + 5 ))"
    if create_vm $NEXT_VMID "n8n-$NODE" $N8N_CORES $N8N_MEMORY $N8N_DISK $N8N_IP false; then
        SERVICE_VMS+=("n8n:$NEXT_VMID:$N8N_IP")
        add_lb_service_config "n8n" 5678 $N8N_IP 5678 "http" "option httpchk GET /healthz" # Use appropriate health check endpoint
        VM_ID_BASE=$((NEXT_VMID + 1))
    else
        echo "N8N VM deployment failed."
    fi
fi

# Deploy Redis VM
if [ "$DEPLOY_REDIS" = true ]; then
    echo ""
    echo "=== Deploying Redis VM ==="
    NEXT_VMID=$(get_next_vmid $VM_ID_BASE)
    REDIS_IP="${IP_BASE}.$(( NEXT_VMID % 250 + 5 ))"
    if create_vm $NEXT_VMID "redis-$NODE" $REDIS_CORES $REDIS_MEMORY $REDIS_DISK $REDIS_IP false; then
        SERVICE_VMS+=("redis:$NEXT_VMID:$REDIS_IP")
        add_lb_service_config "redis" 6379 $REDIS_IP 6379 "tcp"
        VM_ID_BASE=$((NEXT_VMID + 1))
    else
        echo "Redis VM deployment failed."
    fi
fi

# Deploy PostgreSQL VM
if [ "$DEPLOY_POSTGRES" = true ]; then
    echo ""
    echo "=== Deploying PostgreSQL VM ==="
    NEXT_VMID=$(get_next_vmid $VM_ID_BASE)
    POSTGRES_IP="${IP_BASE}.$(( NEXT_VMID % 250 + 5 ))"
    if create_vm $NEXT_VMID "postgres-$NODE" $POSTGRES_CORES $POSTGRES_MEMORY $POSTGRES_DISK $POSTGRES_IP false; then
        SERVICE_VMS+=("postgres:$NEXT_VMID:$POSTGRES_IP")
        add_lb_service_config "postgres" 5432 $POSTGRES_IP 5432 "tcp"
        VM_ID_BASE=$((NEXT_VMID + 1))
    else
        echo "PostgreSQL VM deployment failed."
    fi
fi

# --- Deploy Load Balancer VM ---
declare LB_VMID # Declare LB_VMID
declare LB_IP   # Declare LB_IP
LB_SETUP_SCRIPT="" # Path to the generated script

if [ "$DEPLOY_LB" = true ]; then
    echo ""
    echo "=== Deploying Load Balancer VM ==="
    LB_VMID=$(get_next_vmid $LB_VMID_BASE) # Use separate base ID for LB
    LB_IP="${IP_BASE}.$(( LB_VMID % 250 + 5 ))" # Assign IP similarly

    if create_vm $LB_VMID "loadbalancer-$NODE" $LB_CORES $LB_MEMORY $LB_DISK $LB_IP false; then
        echo "Load balancer VM $LB_VMID created successfully."
        # Generate HAProxy setup script using the collected service configs
        generate_lb_setup $LB_VMID "$LB_CONFIG_SERVICES"
        LB_SETUP_SCRIPT="/tmp/lb-setup-$LB_VMID.sh" # Store path for summary

        # Enable HA if requested
        if [ "$HA_ENABLED" = true ]; then
            if ! enable_ha $LB_VMID $HA_GROUP; then
                echo "Warning: Failed to enable HA for Load Balancer VM $LB_VMID."
            fi
        fi
        echo "Load balancer VM preparation complete."
    else
        echo "Load Balancer VM deployment failed."
        DEPLOY_LB=false # Mark as not deployed if creation failed
    fi
fi

# ======= SUMMARY =======
echo ""
echo "======================== Deployment Summary ========================"
echo "Node: $NODE"
echo "--------------------------------------------------------------------"

if [ ${#SERVICE_VMS[@]} -gt 0 ]; then
    echo "Service VMs Deployed:"
    printf "%-10s | %-7s | %-15s\n" "Service" "VM ID" "IP Address"
    echo "--------------------------------------------------"
    for svc_info in "${SERVICE_VMS[@]}"; do
        IFS=':' read -r name vmid ip <<< "$svc_info"
        printf "%-10s | %-7s | %-15s\n" "$name" "$vmid" "$ip"
    done
else
    echo "No service VMs were deployed or created successfully on this run."
fi

echo "--------------------------------------------------------------------"

if [ "$DEPLOY_LB" = true ]; then
    printf "Load Balancer VM: ID %-7s | IP %-15s\n" "$LB_VMID" "$LB_IP"
    if [ "$HA_ENABLED" = true ]; then
        echo "HA Status: Enabled in group '$HA_GROUP'"
    else
        echo "HA Status: Disabled"
    fi
    echo "HAProxy setup script: $LB_SETUP_SCRIPT (Needs manual transfer & execution)"
else
    echo "Load Balancer VM: Not deployed on this node."
fi
echo "===================================================================="

echo ""
echo "=========================== Next Steps ==========================="
echo "1. Check the Proxmox UI for the status of the created VMs (IDs listed above)."
echo "2. Start the VMs if they are not already running."
echo "3. Access the VM consoles via Proxmox UI to complete the Ubuntu Server installation."
echo "   (Follow the on-screen prompts, ensure networking is configured if cloud-init didn't fully apply)."
echo "4. For Service VMs (Ollama, Qdrant, etc.):"
echo "   -> After OS installation, SSH into each VM (e.g., ssh ubuntu@<VM_IP>)."
echo "   -> Install and configure the required service software (Docker, Ollama, Qdrant, etc.)."
echo "   -> Refer to the official documentation for each service."
if [ "$DEPLOY_LB" = true ] && [ -n "$LB_SETUP_SCRIPT" ]; then
echo "5. For the Load Balancer VM (ID $LB_VMID):"
echo "   -> After OS installation, copy the setup script to the VM:"
echo "      scp $LB_SETUP_SCRIPT ubuntu@$LB_IP:/home/ubuntu/"
echo "   -> SSH into the Load Balancer VM:"
echo "      ssh ubuntu@$LB_IP"
echo "   -> Run the setup script with sudo:"
echo "      sudo /home/ubuntu/$(basename $LB_SETUP_SCRIPT)"
echo "   -> Verify HAProxy status: sudo systemctl status haproxy"
echo "   -> Access HAProxy stats page (if enabled): http://$LB_IP:8404/stats (User: admin, Pass: san-o1-admin)"
fi
echo "6. GPU Passthrough (Ollama VM):"
echo "   -> Ensure the Proxmox HOST was rebooted if IOMMU settings were changed."
echo "   -> Inside the Ollama VM, install NVIDIA drivers appropriate for the passed-through GPU."
echo "   -> Verify GPU detection within the VM (e.g., using nvidia-smi)."
echo "===================================================================="
echo "Script finished."