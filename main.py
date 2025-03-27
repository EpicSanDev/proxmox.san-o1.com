#!/usr/bin/env python3
"""
Proxmox AI Infrastructure Deployment Script
This script automates the deployment of AI infrastructure on Proxmox,
including node selection, resource allocation, and load balancing.
"""

import os
import sys
import yaml
import logging
import argparse
from proxmox_api import ProxmoxAPI
from node_selector import NodeSelector
from load_balancer import LoadBalancer
from service_deployer import ServiceDeployer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('san-o1-deployer')

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Deploy AI infrastructure on Proxmox')
    parser.add_argument('--config', type=str, default='config.yaml',
                        help='Path to configuration file')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')
    return parser.parse_args()

def load_config(config_path):
    """Load configuration from YAML file."""
    try:
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    except Exception as e:
        logger.error(f"Failed to load configuration: {str(e)}")
        sys.exit(1)

def main():
    """Main execution function."""
    # Parse arguments
    args = parse_arguments()
    
    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Load configuration
    config = load_config(args.config)
    logger.info(f"Loaded configuration from {args.config}")
    
    # Initialize Proxmox API
    proxmox = ProxmoxAPI(
        host=config['proxmox']['host'],
        user=config['proxmox']['user'],
        password=config['proxmox']['password'],
        verify_ssl=config['proxmox'].get('verify_ssl', False)
    )
    logger.info(f"Connected to Proxmox host: {config['proxmox']['host']}")
    
    # Initialize node selector with AI capabilities
    node_selector = NodeSelector(proxmox, config['node_selection'])
    
    # Initialize load balancer
    load_balancer = LoadBalancer(proxmox, config['load_balancing'])
    
    # Initialize service deployer
    service_deployer = ServiceDeployer(proxmox, config['services'])
    
    # Analyze nodes and select optimal placement
    node_allocations = node_selector.analyze_and_allocate()
    logger.info(f"Determined optimal node allocations: {node_allocations}")
    
    # Deploy services to selected nodes
    deployment_results = service_deployer.deploy_services(node_allocations)
    logger.info("Service deployment completed")
    
    # Configure load balancing
    load_balancer.configure(deployment_results)
    logger.info("Load balancing configuration completed")
    
    # Print summary
    print("\n===== Deployment Summary =====")
    for service_name, result in deployment_results.items():
        if 'error' in result:
            print(f" - {service_name}: Deployment failed on {result.get('node', 'unknown')} - {result['error']}")
        elif 'node' in result and 'id' in result:
            print(f" - {service_name}: Deployed on {result['node']} (ID: {result['id']})")
        else:
            print(f" - {service_name}: Deployment status unknown")
    
    print("\n===== Access Information =====")
    for service_name, result in deployment_results.items():
        if 'access_url' in result:
            print(f" - {service_name}: {result['access_url']}")
            if 'credentials' in result:
                creds = result['credentials']
                if 'username' in creds:
                    print(f"   Username: {creds['username']}")
                if 'password' in creds:
                    print(f"   Password: {creds['password']}")
    
    print("\nDeployment completed successfully!")

if __name__ == "__main__":
    main()
