#!/usr/bin/env python3
"""
San-O1 Proxmox AI Infrastructure GitHub Direct Deployment
This script allows for direct execution of the deployment process from GitHub.
"""

import os
import sys
import argparse
import subprocess
import tempfile
import shutil
import yaml
import logging
import requests
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('san-o1-github-deployer')

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Deploy AI infrastructure to Proxmox directly from GitHub")
    parser.add_argument('--config', type=str, 
                      help="Path to local config file (if not provided, will use default from repo)")
    parser.add_argument('--branch', type=str, default='main',
                      help="GitHub branch to use (default: main)")
    parser.add_argument('--repo', type=str, default='bastienjavaux/san-o1-proxmox-deployer',
                      help="GitHub repository (default: bastienjavaux/san-o1-proxmox-deployer)")
    parser.add_argument('--debug', action='store_true',
                      help="Enable debug logging")
    return parser.parse_args()

def check_requirements():
    """Check if system requirements are met."""
    requirements = ['git', 'python3', 'pip']
    missing = []
    
    for cmd in requirements:
        try:
            subprocess.run(['which', cmd], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            missing.append(cmd)
    
    if missing:
        logger.info(f"Installing missing requirements: {', '.join(missing)}")
        try:
            subprocess.run(['apt-get', 'update'], check=True)
            subprocess.run(['apt-get', 'install', '-y'] + missing, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install requirements: {e}")
            sys.exit(1)

def clone_repository(repo, branch, temp_dir):
    """Clone the GitHub repository to a temporary directory."""
    logger.info(f"Cloning repository {repo} (branch: {branch}) to {temp_dir}")
    try:
        repo_url = f"https://github.com/{repo}.git"
        subprocess.run(['git', 'clone', '-b', branch, repo_url, temp_dir], check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone repository: {e}")
        return False

def install_dependencies(temp_dir):
    """Install required Python dependencies."""
    requirements_file = os.path.join(temp_dir, 'requirements.txt')
    if not os.path.exists(requirements_file):
        logger.error(f"Requirements file not found: {requirements_file}")
        return False
    
    logger.info("Installing Python dependencies")
    try:
        subprocess.run(['pip', 'install', '-r', requirements_file], check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install dependencies: {e}")
        return False

def prepare_config(temp_dir, local_config_path):
    """Prepare the configuration file."""
    config_file = os.path.join(temp_dir, 'config.yaml')
    example_config = os.path.join(temp_dir, 'config.yaml.example')
    
    # If local config is provided, use it
    if local_config_path:
        if os.path.exists(local_config_path):
            logger.info(f"Using local config file: {local_config_path}")
            shutil.copy(local_config_path, config_file)
            return True
        else:
            logger.error(f"Local config file not found: {local_config_path}")
            return False
    
    # If config.yaml doesn't exist but example does, copy it and prompt user
    if not os.path.exists(config_file) and os.path.exists(example_config):
        shutil.copy(example_config, config_file)
        logger.info(f"Created default config file: {config_file}")
        logger.info("Please edit the config file with your Proxmox settings before continuing.")
        
        # Open the file for editing
        try:
            # Try to use a GUI editor if available (for desktop environments)
            editors = ["nano", "vim", "vi"]
            editor_found = False
            
            for editor in editors:
                try:
                    subprocess.run(["which", editor], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    logger.info(f"Opening config with {editor}. Please edit and save.")
                    subprocess.run([editor, config_file])
                    editor_found = True
                    break
                except subprocess.CalledProcessError:
                    continue
                
            if not editor_found:
                logger.warning("No text editor found. Please edit the config file manually.")
            
            # Ask if ready to continue
            response = input("Have you finished editing the config file? (yes/no): ").lower()
            if response != 'yes' and response != 'y':
                logger.info("Deployment aborted. Edit the config file and run again.")
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Error opening editor: {e}")
            logger.info(f"Please edit {config_file} manually and then run the script again.")
            return False
    
    # If config.yaml exists, use it
    if os.path.exists(config_file):
        logger.info(f"Using existing config file: {config_file}")
        return True
    
    logger.error("No config file found and couldn't create one.")
    return False

def execute_deployment(temp_dir):
    """Execute the deployment script."""
    main_script = os.path.join(temp_dir, 'main.py')
    config_file = os.path.join(temp_dir, 'config.yaml')
    
    if not os.path.exists(main_script):
        logger.error(f"Main script not found: {main_script}")
        return False
    
    logger.info("Starting deployment process")
    try:
        subprocess.run(['python3', main_script, '--config', config_file], check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Deployment failed: {e}")
        return False

def main():
    """Main function to execute GitHub-based deployment."""
    args = parse_arguments()
    
    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    print("\n" + "="*60)
    print(" San-O1 Proxmox AI Infrastructure Deployer - GitHub Direct Mode ")
    print("="*60 + "\n")
    
    # Check system requirements
    logger.info("Checking system requirements")
    check_requirements()
    
    # Create temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Using temporary directory: {temp_dir}")
        
        # Clone repository
        if not clone_repository(args.repo, args.branch, temp_dir):
            sys.exit(1)
        
        # Install dependencies
        if not install_dependencies(temp_dir):
            sys.exit(1)
        
        # Prepare configuration
        if not prepare_config(temp_dir, args.config):
            sys.exit(1)
        
        # Execute deployment
        if not execute_deployment(temp_dir):
            sys.exit(1)
        
        logger.info("Deployment completed successfully!")
        print("\n" + "="*60)
        print(" Deployment Complete! ")
        print("="*60 + "\n")

if __name__ == "__main__":
    main()
