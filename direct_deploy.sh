#!/bin/bash
#
# San-O1 Proxmox AI Infrastructure Direct Deployer
# This script downloads and executes the GitHub-based deployment script
# Run directly in the Proxmox cluster console.
#

set -e

# Print banner
echo "================================================================="
echo "      San-O1 Proxmox AI Infrastructure Direct Deployer"
echo "================================================================="
echo ""
echo "This script will download and execute the San-O1 deployment code"
echo "directly from GitHub."
echo ""

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root" >&2
    exit 1
fi

# Install dependencies if needed
install_deps() {
    echo "Installing required dependencies..."
    apt-get update
    apt-get install -y curl python3 python3-pip git
}

# Check for dependencies
if ! command -v curl &> /dev/null || ! command -v python3 &> /dev/null || ! command -v git &> /dev/null; then
    echo "Some dependencies are missing. Installing..."
    install_deps
fi

# Create a temporary directory
TEMP_DIR=$(mktemp -d)
echo "Working in temporary directory: $TEMP_DIR"
cd $TEMP_DIR

# Download the GitHub deployment script
echo "Downloading deployment script from GitHub..."
curl -s -L -o github_direct_deploy.py https://raw.githubusercontent.com/bastienjavaux/san-o1-proxmox-deployer/main/github_direct_deploy.py

# Make it executable
chmod +x github_direct_deploy.py

# Run the deployment script
echo "Starting deployment process..."
python3 github_direct_deploy.py "$@"

# Clean up
echo "Cleaning up..."
cd /
rm -rf $TEMP_DIR

echo "================================================================="
echo "                 Deployment process complete"
echo "================================================================="
