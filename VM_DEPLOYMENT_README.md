# San-O1 VM-Based Deployment Guide

This guide explains how to deploy the San-O1 AI infrastructure using VMs instead of containers, with GPU integration and HA load balancing.

## Why VMs instead of Containers?

1. **Better GPU integration** - Direct GPU passthrough to VMs provides better performance and compatibility with AI models
2. **Full system control** - VMs offer full operating system control for complex configurations
3. **Stronger isolation** - Enhanced security with complete VM isolation
4. **Flexibility for GPU drivers** - Install and update GPU drivers within the VM without affecting the host
5. **Support for HA features** - Better support for Proxmox's High Availability features

## Prerequisites

- Proxmox VE cluster setup
- Ubuntu 22.04 Server ISO uploaded to your Proxmox storage
- Physical GPU(s) installed in at least one Proxmox node for Ollama VM
- Network connectivity between all nodes
- Basic knowledge of Proxmox administration

## Deployment Steps

### 1. Prepare the Script

1. Upload the `vm_deploy.sh` script to each Proxmox node where you want to deploy services
2. Make the script executable:
   ```bash
   chmod +x vm_deploy.sh
   ```

### 2. Configure the Environment

Edit the script to match your environment or export variables before running:

```bash
# Required configuration
export PROXMOX_PASSWORD="your-secure-password"
export IP_BASE="192.168.1"  # Your network's IP base
export GATEWAY="192.168.1.1"  # Your network's gateway

# Optional configuration
export UBUNTU_ISO="local:iso/ubuntu-22.04-live-server-amd64.iso"
export VM_STORAGE="local-lvm"  # Storage for VM disks
```

### 3. Run the Script on Each Node

Execute the script on each Proxmox node where you want to deploy services:

```bash
./vm_deploy.sh
```

Follow the interactive prompts to select which services to deploy on the current node.

### 4. Configure High Availability for Load Balancer

If you selected to enable HA for the load balancer during script execution, the VM will be automatically added to the HA group. You can verify this in the Proxmox web UI under Datacenter > HA.

### 5. Complete VM Setup

After the script completes:

1. Start each VM from the Proxmox web UI
2. Complete the Ubuntu installation
3. For the load balancer VM:
   - Transfer the generated HAProxy setup script to the VM
   - Run the script to configure HAProxy

### 6. Service Installation

For each service VM, install the respective software:

#### Ollama VM

```bash
# Update and install dependencies
apt update && apt upgrade -y
apt install -y build-essential

# Install NVIDIA drivers if using NVIDIA GPU
apt install -y nvidia-driver-535 nvidia-cuda-toolkit

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull models
ollama pull deepseek:32b
```

#### Qdrant VM

```bash
# Update and install dependencies
apt update && apt upgrade -y

# Install Qdrant
curl -L https://github.com/qdrant/qdrant/releases/download/v1.8.0/qdrant-amd64.deb -o qdrant.deb
apt install -y ./qdrant.deb
systemctl enable --now qdrant
```

#### Other services

Follow similar installation procedures for N8N, Redis, and PostgreSQL.

## GPU Passthrough Configuration

### IOMMU Configuration

1. Enable IOMMU in the host's BIOS/UEFI settings
2. Add kernel parameters to the host's GRUB configuration:

   For Intel processors:
   ```
   intel_iommu=on iommu=pt
   ```
   
   For AMD processors:
   ```
   amd_iommu=on iommu=pt
   ```

3. Update GRUB and reboot the host:
   ```bash
   update-grub
   reboot
   ```

### Verifying GPU Passthrough

After VM creation and boot:

1. Connect to the Ollama VM
2. Verify GPU is detected:
   ```bash
   lspci | grep -i nvidia
   nvidia-smi
   ```

3. Test Ollama with GPU acceleration:
   ```bash
   ollama run deepseek:32b "How to know if GPU acceleration is working?"
   ```

## Load Balancer High Availability

The load balancer VM can be configured with Proxmox HA to ensure service continuity:

1. Create an HA group in Proxmox with multiple nodes
2. Add the load balancer VM to the HA group
3. Set the HA policy to "started"
4. Configure fencing for automatic failover

## Distributed Service Architecture

For a production environment, consider distributing your services across multiple nodes:

- Node 1: Ollama (with GPU), Load Balancer
- Node 2: Qdrant, Redis
- Node 3: N8N, PostgreSQL

This distribution ensures better resource utilization and fault tolerance.

## Maintenance and Monitoring

1. Regular VM backups can be configured through Proxmox
2. Monitor VM resources through Proxmox dashboard
3. For service-specific monitoring, install Prometheus and Grafana on a separate monitoring VM

## Troubleshooting

### GPU Passthrough Issues

- Verify IOMMU is enabled in BIOS and kernel
- Check that the GPU is not in use by the host (blacklist the driver on the host)
- Ensure VM configuration includes correct PCI device passthrough

### Network Connectivity Issues

- Verify IP addressing and gateway configuration
- Check firewall settings on VMs and host
- Test connectivity between services with ping and telnet

### Load Balancer Issues

- Verify HAProxy configuration
- Check that all backend services are reachable
- Review HAProxy logs: `/var/log/haproxy.log`

## Conclusion

This VM-based deployment approach provides better GPU integration for AI workloads while maintaining the flexibility and power of the San-O1 infrastructure. The use of VMs instead of containers allows for direct hardware access, which is crucial for GPU-accelerated AI models.
