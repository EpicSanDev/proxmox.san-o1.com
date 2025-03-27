# San-O1 Proxmox AI Infrastructure Deployer

Automated deployment and management of AI infrastructure on Proxmox VE with intelligent node selection, resource allocation, and load balancing.

## Quick Start

Run this command in your Proxmox console to start the deployment:

```bash
bash <(curl -s https://raw.githubusercontent.com/bastienjavaux/san-o1-proxmox-deployer/main/san-o1-deploy.sh)
```

## Overview

This tool automates the deployment of a complete AI infrastructure stack on Proxmox Virtual Environment, including:

- **Qdrant** vector database for embeddings and similarity search
- **Ollama** with deepseek:32B model and NVIDIA GPU support
- **n8n** workflow automation platform
- **Redis** in-memory database
- **PostgreSQL** database
- Automatic load balancing with HAProxy

The system uses intelligent resource allocation to select the optimal nodes for each service based on resource availability, GPU requirements, and specified affinity/anti-affinity rules.

## Features

- 🧠 **AI-Driven Node Selection**: Analyzes your Proxmox cluster and selects the optimal node for each service based on resource availability and requirements.
- 🔄 **Automated Load Balancing**: Configures HAProxy for service load balancing with automatic failover.
- 🎯 **Resource Optimization**: Efficiently allocates resources based on service requirements and node capabilities.
- 🎛️ **GPU Awareness**: Automatically places GPU-intensive services like Ollama on nodes with NVIDIA GPUs.
- 🔌 **Service Affinity**: Keeps related services together to minimize network latency.
- 🛡️ **Service Anti-Affinity**: Separates specified services for enhanced reliability.
- 📊 **Detailed Reporting**: Provides comprehensive summaries of the deployment.

## Requirements

- Proxmox VE 7.x or later
- Python 3.8 or newer
- A Proxmox cluster with at least one node
- NVIDIA GPU (for Ollama with deepseek:32B)
- At least 64GB RAM across the cluster
- At least 200GB free disk space

## Installation

### Method 1: Direct Script (Recommended)

Run this command in your Proxmox console:

```bash
bash <(curl -s https://raw.githubusercontent.com/bastienjavaux/san-o1-proxmox-deployer/main/san-o1-deploy.sh)
```

### Method 2: Manual Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/bastienjavaux/san-o1-proxmox-deployer.git
   cd san-o1-proxmox-deployer
   ```

2. Install required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Copy and customize the configuration:
   ```bash
   cp config.yaml.example config.yaml
   # Edit config.yaml with your settings
   ```

4. Run the deployment script:
   ```bash
   python main.py --config config.yaml
   ```

## Configuration

The `config.yaml` file controls all aspects of the deployment. Key sections include:

### Proxmox Connection Settings

```yaml
proxmox:
  host: "your-proxmox-server.example.com"
  user: "root@pam"
  password: "your-password"
  verify_ssl: false
```

### Node Selection Settings

Configure how the system evaluates and selects nodes:

```yaml
node_selection:
  node_weights:
    cpu: 0.3
    memory: 0.3
    disk: 0.2
    network: 0.1
    gpu: 0.1
```

### Service Requirements

Define resource requirements for each service:

```yaml
service_requirements:
  ollama:
    memory: 32768  # 32GB for large models
    cpu: 8
    disk: 100
    gpu: "nvidia"  # Requires NVIDIA GPU
```

See the comments in `config.yaml.example` for detailed explanations of all options.

## Troubleshooting

### Common Issues

**Connection Failed to Proxmox**
- Verify your Proxmox server address, username, and password in the config
- Ensure your user has API access rights in Proxmox

**No GPU-Compatible Node Found**
- Check that your NVIDIA GPU is properly installed and visible in Proxmox
- Verify that the GPU is not currently in use by another VM/container

**Resource Allocation Failures**
- Increase the available resources in your Proxmox cluster
- Adjust the service resource requirements in the config file

**Container Creation Failures**
- Verify that the specified template exists on your Proxmox server
- Check Proxmox logs for detailed error messages

## License

This project is licensed under the MIT License.
