# GPU Passthrough Guide for Ollama VM

This guide provides detailed instructions for configuring GPU passthrough from your Proxmox host to an Ollama VM, enabling hardware acceleration for large language models.

## Prerequisites

- Proxmox VE 7.0+ installed
- A compatible GPU (NVIDIA or AMD)
- IOMMU-capable CPU and motherboard
- Proper driver support

## Step 1: Enable IOMMU on the Proxmox Host

First, you need to enable IOMMU at the BIOS/UEFI level and in the kernel:

### BIOS/UEFI Configuration

1. Enter your server's BIOS/UEFI configuration
2. Look for settings related to:
   - Intel: "VT-d", "Virtualization Technology for Directed I/O"
   - AMD: "AMD-Vi", "SVM Mode", "IOMMU"
3. Enable these settings
4. Save and exit BIOS/UEFI

### Kernel Configuration

1. SSH into your Proxmox host
2. Edit the GRUB configuration:

```bash
nano /etc/kernel/cmdline
```

3. Add the appropriate parameter:
   - For Intel CPUs:
   ```
   intel_iommu=on iommu=pt
   ```
   - For AMD CPUs:
   ```
   amd_iommu=on iommu=pt
   ```

4. Update the initramfs:
```bash
update-initramfs -u -k all
```

5. Reboot the server:
```bash
reboot
```

6. Verify IOMMU is enabled:
```bash
dmesg | grep -e IOMMU -e DMAR
```

## Step 2: Identify and Prepare the GPU

First, identify the GPU device ID:

```bash
lspci | grep -i 'vga\|nvidia\|amd'
```

This will output something like:
```
01:00.0 VGA compatible controller: NVIDIA Corporation TU102 [GeForce RTX 2080 Ti] (rev a1)
01:00.1 Audio device: NVIDIA Corporation TU102 High Definition Audio Controller (rev a1)
```

Note the PCI address (e.g., `01:00.0` for the GPU and `01:00.1` for its audio component).

### Blacklist GPU Drivers on Host

To prevent the host from using the GPU, create a blacklist file:

```bash
cat > /etc/modprobe.d/blacklist-nvidia.conf << EOF
blacklist nouveau
blacklist nvidia
blacklist radeon
EOF
```

For NVIDIA cards, also add the GPU to the vfio configuration:

```bash
cat > /etc/modprobe.d/vfio.conf << EOF
options vfio-pci ids=10de:1e04,10de:10f7
EOF
```

Replace `10de:1e04,10de:10f7` with your GPU's vendor:device ID pairs, which you can find with:

```bash
lspci -n -s 01:00.0
lspci -n -s 01:00.1
```

Then update initramfs again:

```bash
update-initramfs -u -k all
reboot
```

## Step 3: Create the Ollama VM with GPU Passthrough

You can use our `vm_deploy.sh` script to automatically create the VM with GPU passthrough, or follow these steps manually:

### Manual VM Creation in Proxmox Web UI

1. Create a new VM with the following settings:
   - OS: Ubuntu 22.04
   - Cores: 8 (or more for better performance)
   - Memory: 32GB (or more for larger models)
   - Disk: 100GB
   - Network: virtio

2. After VM creation, stop the VM

3. Edit the VM's configuration:
   - Go to the "Hardware" tab
   - Click "Add" → "PCI Device"
   - Select the GPU from the dropdown (it will show the PCI address)
   - Check "All Functions" to include the audio component
   - Enable "ROM-Bar" and "PCI-Express"
   - Click Add

4. Start the VM

## Step 4: Install GPU Drivers in the VM

Connect to the VM via SSH or console and install the appropriate drivers:

### For NVIDIA GPUs

```bash
# Update system
apt update && apt upgrade -y

# Install prerequisites
apt install -y build-essential

# Install NVIDIA drivers
apt install -y nvidia-driver-535 nvidia-cuda-toolkit

# Verify installation
nvidia-smi
```

### For AMD GPUs

```bash
# Update system
apt update && apt upgrade -y

# Install prerequisites
apt install -y build-essential

# Install AMD drivers
apt install -y amdgpu-pro

# Verify installation
rocminfo
```

## Step 5: Install and Configure Ollama

Now install Ollama on the VM:

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull your preferred model
ollama pull deepseek:32b
```

## Step 6: Verify GPU Acceleration

To verify that Ollama is using the GPU:

```bash
# Run a simple query that will use the GPU
ollama run deepseek:32b "How do I know if GPU acceleration is working?"
```

While this is running, in another terminal:

```bash
# Monitor GPU usage
nvidia-smi -l 1
```

You should see GPU memory being used and the GPU load increasing.

## Troubleshooting

### GPU Not Detected in VM

1. Verify IOMMU is properly enabled:
   ```bash
   dmesg | grep -e IOMMU -e DMAR
   ```

2. Check if the GPU is properly bound to vfio-pci on the host:
   ```bash
   lspci -nnv | grep -i vfio
   ```

3. Ensure the VM is configured with machine type q35 (supports PCI Express):
   ```bash
   qm set <vmid> --machine q35
   ```

### NVIDIA Error "NVIDIA-SMI has failed"

This usually indicates the GPU is not properly passed through or there's a driver issue.

1. Check if the GPU is visible in the VM:
   ```bash
   lspci | grep -i nvidia
   ```

2. Try reinstalling the NVIDIA drivers with the appropriate options:
   ```bash
   apt purge -y nvidia-*
   apt install -y nvidia-driver-535 --no-install-recommends
   ```

### Poor Performance

1. Ensure your VM has enough CPU cores and memory
2. Check for thermal throttling:
   ```bash
   nvidia-smi -q -d TEMPERATURE
   ```

3. Optimize VM configuration:
   - Use host CPU model
   - Enable nested virtualization
   - Use hugepages for memory

## Conclusion

With properly configured GPU passthrough, your Ollama VM can fully utilize the GPU for hardware acceleration, significantly improving performance for AI model inference compared to container-based deployments.

The improved performance and direct hardware access make the VM approach superior for AI workloads that require GPU acceleration.
