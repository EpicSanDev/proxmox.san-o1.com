#!/bin/bash
#
# San-O1 Proxmox AI Infrastructure Deployment Script
# This script pulls the San-O1 deployment code from GitHub and executes it.
# Can be run directly in the Proxmox cluster console.
#

set -e

echo "=== San-O1 Proxmox AI Infrastructure Deployer ==="
echo "=== Starting deployment process... ==="

# Create temporary directory
TEMP_DIR=$(mktemp -d)
cd $TEMP_DIR

echo "Working in temporary directory: $TEMP_DIR"

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Installing git..."
    apt-get update
    apt-get install -y git python3 python3-pip
fi

# Clone the repository
echo "Cloning San-O1 deployment repository from GitHub..."
git clone https://github.com/bastienjavaux/san-o1-proxmox-deployer.git
cd san-o1-proxmox-deployer

# Install dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements.txt

# Check for config file
if [ -f "config.yaml" ]; then
    echo "Using existing config.yaml"
else
    echo "Creating default config.yaml"
    cp config.yaml.example config.yaml
    echo "Please edit config.yaml with your Proxmox settings and re-run this script."
    echo "Config file location: $TEMP_DIR/san-o1-proxmox-deployer/config.yaml"
    exit 1
fi

# Run the deployment
echo "Starting deployment..."
python3 main.py --config config.yaml

echo "Deployment complete!"
