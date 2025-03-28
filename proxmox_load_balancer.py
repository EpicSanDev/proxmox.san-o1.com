#!/usr/bin/env python3
"""
Proxmox Intelligent Load Balancer

Ce script fournit un équilibreur de charge intelligent pour un cluster Proxmox,
utilisant l'analyse prédictive et l'apprentissage pour optimiser la distribution
des machines virtuelles entre les nœuds du cluster.
"""

import argparse
import json
import sys
import os
import logging
import time
from proxmox_api import ProxmoxAPI
from node_selector import NodeSelector
from load_balancer import LoadBalancer

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Proxmox Intelligent Load Balancer")
    
    # Connection options
    connection_group = parser.add_argument_group("Connection Options")
    connection_group.add_argument("--host", required=True, help="Proxmox host (IP or hostname)")
    connection_group.add_argument("--user", required=True, help="Proxmox API username")
    connection_group.add_argument("--password", help="Proxmox API password (omit for prompt)")
    connection_group.add_argument("--realm", default="pam", help="Authentication realm (default: pam)")
    connection_group.add_argument("--port", type=int, default=8006, help="API port (default: 8006)")
    connection_group.add_argument("--verify-ssl", action="store_true", help="Verify SSL certificate")
    
    # Operation mode
    mode_group = parser.add_argument_group("Operation Mode")
    mode_subgroup = mode_group.add_mutually_exclusive_group(required=True)
    mode_subgroup.add_argument("--daemon", action="store_true", help="Run as a daemon process")
    mode_subgroup.add_argument("--once", action="store_true", help="Run balance once and exit")
    mode_subgroup.add_argument("--status", action="store_true", help="Show cluster status")
    mode_subgroup.add_argument("--recommendations", action="store_true", help="Show migration recommendations")
    mode_subgroup.add_argument("--config", action="store_true", help="Modify configuration and exit")
    mode_subgroup.add_argument("--configure-proxmox", action="store_true", help="Configure Proxmox for load balancing")
    mode_subgroup.add_argument("--check-proxmox", action="store_true", help="Check Proxmox configuration status")
    mode_subgroup.add_argument("--update-critical-vms", action="store_true", help="Update the list of critical VMs")
    
    # Configuration options
    config_group = parser.add_argument_group("Configuration Options")
    config_group.add_argument("--config-file", default="load_balancer_config.json", 
                           help="Path to configuration file (default: load_balancer_config.json)")
    config_group.add_argument("--save-config", action="store_true", 
                           help="Save modified configuration to file")
    
    # Load balancer settings (only used with --config)
    settings_group = parser.add_argument_group("Load Balancer Settings")
    settings_group.add_argument("--check-interval", type=int, 
                              help="Seconds between balance checks")
    settings_group.add_argument("--high-load-threshold", type=float, 
                              help="CPU/Memory usage threshold for high load")
    settings_group.add_argument("--low-load-threshold", type=float, 
                              help="CPU/Memory usage threshold for low load")
    settings_group.add_argument("--min-balance-interval", type=int, 
                              help="Minimum seconds between migrations for the same VM")
    settings_group.add_argument("--max-parallel-migrations", type=int, 
                              help="Maximum concurrent migrations")
    
    # VM and node exclusions
    exclusion_group = parser.add_argument_group("Exclusions")
    exclusion_group.add_argument("--exclude-vm", action="append", 
                               help="Exclude VM from balancing (can be used multiple times)")
    exclusion_group.add_argument("--exclude-node", action="append", 
                               help="Exclude node from balancing (can be used multiple times)")
    
    # Proxmox configuration options
    proxmox_config_group = parser.add_argument_group("Proxmox Configuration Options")
    proxmox_config_group.add_argument("--auto-configure-proxmox", type=str, choices=["true", "false"],
                                    help="Enable/disable automatic Proxmox configuration")
    proxmox_config_group.add_argument("--configure-ha", type=str, choices=["true", "false"],
                                    help="Configure High Availability (HA)")
    proxmox_config_group.add_argument("--configure-migration", type=str, choices=["true", "false"],
                                    help="Configure migration settings")
    proxmox_config_group.add_argument("--ha-group-name", 
                                    help="Name for the HA group")
    proxmox_config_group.add_argument("--critical-vm", action="append", 
                                    help="Add VM ID to critical VMs (can be used multiple times)")
    
    return parser.parse_args()

def update_config_from_args(config, args):
    """Update configuration from command line arguments"""
    # Simple parameters
    for param, config_key in [
        ("check_interval", "check_interval"),
        ("high_load_threshold", "high_load_threshold"),
        ("low_load_threshold", "low_load_threshold"),
        ("min_balance_interval", "min_balance_interval"),
        ("max_parallel_migrations", "max_parallel_migrations")
    ]:
        value = getattr(args, param.replace("-", "_"), None)
        if value is not None:
            config[config_key] = value
    
    # Exclusions
    if args.exclude_vm:
        # Combine existing and new exclusions
        config["vm_exclusions"] = list(set(config["vm_exclusions"] + args.exclude_vm))
    
    if args.exclude_node:
        # Combine existing and new exclusions
        config["node_exclusions"] = list(set(config["node_exclusions"] + args.exclude_node))
    
    # Proxmox configuration
    if args.auto_configure_proxmox is not None:
        config["auto_configure_proxmox"] = args.auto_configure_proxmox.lower() == "true"
    
    if "proxmox_config" not in config:
        config["proxmox_config"] = {}
    
    if args.configure_ha is not None:
        config["proxmox_config"]["configure_ha"] = args.configure_ha.lower() == "true"
    
    if args.configure_migration is not None:
        config["proxmox_config"]["configure_migration"] = args.configure_migration.lower() == "true"
    
    if args.ha_group_name:
        config["proxmox_config"]["ha_group_name"] = args.ha_group_name
    
    if args.critical_vm:
        # Convert to integers if possible
        critical_vms = []
        for vm_id in args.critical_vm:
            try:
                critical_vms.append(int(vm_id))
            except ValueError:
                critical_vms.append(vm_id)
        
        # Combine with existing critical VMs
        if "critical_vms" not in config["proxmox_config"]:
            config["proxmox_config"]["critical_vms"] = []
        
        config["proxmox_config"]["critical_vms"] = list(set(config["proxmox_config"]["critical_vms"] + critical_vms))
    
    return config

def config_interactive(load_balancer):
    """Interactive configuration editor"""
    config = load_balancer.config.copy()
    
    print("\nCurrent Configuration:")
    print(json.dumps(config, indent=2))
    
    print("\nEdit Configuration (press Enter to keep current value):")
    
    # Basic settings
    try:
        new_value = input(f"Check interval ({config['check_interval']} seconds): ")
        if new_value.strip():
            config['check_interval'] = int(new_value)
        
        new_value = input(f"High load threshold ({config['high_load_threshold']}): ")
        if new_value.strip():
            config['high_load_threshold'] = float(new_value)
        
        new_value = input(f"Low load threshold ({config['low_load_threshold']}): ")
        if new_value.strip():
            config['low_load_threshold'] = float(new_value)
        
        new_value = input(f"Min balance interval ({config['min_balance_interval']} seconds): ")
        if new_value.strip():
            config['min_balance_interval'] = int(new_value)
        
        new_value = input(f"Max parallel migrations ({config['max_parallel_migrations']}): ")
        if new_value.strip():
            config['max_parallel_migrations'] = int(new_value)
        
        # Resource weights
        print("\nResource Weights (should sum to 1.0):")
        for resource in ['cpu', 'memory', 'disk', 'network']:
            new_value = input(f"  {resource} weight ({config['resource_weights'][resource]}): ")
            if new_value.strip():
                config['resource_weights'][resource] = float(new_value)
        
        # Validate weights
        total = sum(config['resource_weights'].values())
        if abs(total - 1.0) > 0.01:
            print(f"Warning: Weights sum to {total}, normalizing to 1.0")
            for resource in config['resource_weights']:
                config['resource_weights'][resource] /= total
        
        # VM exclusions
        print("\nVM Exclusions (comma-separated list):")
        current = ', '.join(str(vm) for vm in config['vm_exclusions'])
        new_value = input(f"  Current: {current}\n  New: ")
        if new_value.strip():
            config['vm_exclusions'] = [vm.strip() for vm in new_value.split(',')]
        
        # Node exclusions
        print("\nNode Exclusions (comma-separated list):")
        current = ', '.join(config['node_exclusions'])
        new_value = input(f"  Current: {current}\n  New: ")
        if new_value.strip():
            config['node_exclusions'] = [node.strip() for node in new_value.split(',')]
        
        # Proxmox Auto-Configuration
        print("\nProxmox Auto-Configuration:")
        new_value = input(f"Auto-configure Proxmox ({config.get('auto_configure_proxmox', True)}): ")
        if new_value.lower() in ('true', 'false'):
            config['auto_configure_proxmox'] = new_value.lower() == 'true'
        
        if config.get('auto_configure_proxmox', True):
            # Create proxmox_config if it doesn't exist
            if 'proxmox_config' not in config:
                config['proxmox_config'] = {}
                
            new_value = input(f"Configure HA ({config['proxmox_config'].get('configure_ha', True)}): ")
            if new_value.lower() in ('true', 'false'):
                config['proxmox_config']['configure_ha'] = new_value.lower() == 'true'
                
            new_value = input(f"Configure Migration ({config['proxmox_config'].get('configure_migration', True)}): ")
            if new_value.lower() in ('true', 'false'):
                config['proxmox_config']['configure_migration'] = new_value.lower() == 'true'
                
            new_value = input(f"HA Group Name ({config['proxmox_config'].get('ha_group_name', 'lb-ha-group')}): ")
            if new_value.strip():
                config['proxmox_config']['ha_group_name'] = new_value
                
            # Critical VMs
            print("\nCritical VMs (comma-separated list of VM IDs):")
            current = ', '.join(str(vm) for vm in config['proxmox_config'].get('critical_vms', []))
            new_value = input(f"  Current: {current}\n  New: ")
            if new_value.strip():
                config['proxmox_config']['critical_vms'] = [int(vm.strip()) if vm.strip().isdigit() else vm.strip() 
                                                         for vm in new_value.split(',')]
        
        # Advanced settings
        print("\nAdvanced Settings:")
        new_value = input(f"Consider time of day for migrations ({config['consider_time_of_day']}): ")
        if new_value.lower() in ('true', 'false'):
            config['consider_time_of_day'] = new_value.lower() == 'true'
        
        if config['consider_time_of_day']:
            new_value = input(f"Off hours start ({config['off_hours']['start']}): ")
            if new_value.strip():
                config['off_hours']['start'] = int(new_value)
            
            new_value = input(f"Off hours end ({config['off_hours']['end']}): ")
            if new_value.strip():
                config['off_hours']['end'] = int(new_value)
        
    except KeyboardInterrupt:
        print("\nConfiguration editing cancelled.")
        return None
    
    # Confirm changes
    print("\nNew Configuration:")
    print(json.dumps(config, indent=2))
    
    confirm = input("\nSave this configuration? (y/n): ")
    if confirm.lower() == 'y':
        return config
    else:
        print("Configuration changes discarded.")
        return None

def show_cluster_status(load_balancer):
    """Display cluster status information"""
    print("\nCluster Status:")
    
    # Get node status
    nodes_usage = load_balancer.proxmox_api.get_resource_usage()
    if not nodes_usage:
        print("  No node data available")
        return
    
    # Print node information
    print("\nNodes:")
    print(f"{'Name':<15} {'Status':<10} {'CPU Usage':<10} {'Memory Usage':<15} {'Disk Usage':<15}")
    print("-" * 70)
    
    for node in nodes_usage:
        name = node['name']
        status = node['status']
        cpu = f"{node['cpu']['usage']*100:.1f}%"
        memory = f"{node['memory']['used']/node['memory']['total']*100:.1f}%"
        disk = f"{node['disk']['used']/node['disk']['total']*100:.1f}%"
        
        print(f"{name:<15} {status:<10} {cpu:<10} {memory:<15} {disk:<15}")
    
    # Print VM counts by node
    print("\nVMs by Node:")
    for node in nodes_usage:
        vms = load_balancer.proxmox_api.get_node_vms(node['name']) or []
        running_vms = [vm for vm in vms if vm['status'] == 'running']
        print(f"  {node['name']}: {len(running_vms)} running, {len(vms) - len(running_vms)} stopped")
    
    # Show overloaded and underloaded nodes
    overloaded = load_balancer.detect_overloaded_nodes()
    underloaded = load_balancer.detect_underloaded_nodes()
    
    if overloaded:
        print(f"\nOverloaded Nodes: {', '.join(overloaded)}")
    if underloaded:
        print(f"Underloaded Nodes: {', '.join(underloaded)}")
    
    # Show recent migrations
    if load_balancer.migration_history:
        print("\nRecent Migrations:")
        for migration in load_balancer.migration_history[-5:]:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(migration['timestamp']))
            print(f"  VM {migration['vm_id']} from {migration['source_node']} to {migration['target_node']} at {timestamp}")
    
    # Show critical VMs for HA
    critical_vms = load_balancer.config.get("proxmox_config", {}).get("critical_vms", [])
    if critical_vms:
        print("\nCritical VMs for HA:")
        for vm_id in critical_vms:
            print(f"  VM {vm_id}")

def show_recommendations(load_balancer):
    """Display migration recommendations"""
    recommendations = load_balancer.get_recommendations()
    
    print("\nMigration Recommendations:")
    
    if not recommendations['migrations']:
        print("  No migrations recommended at this time")
    else:
        for rec in recommendations['migrations']:
            vm_id = rec['vm_id']
            vm_name = rec['vm_name']
            source = rec['source_node']
            targets = rec['target_nodes']
            
            print(f"  VM {vm_id} ({vm_name}) on {source}")
            print(f"    Recommended targets: {', '.join(targets)}")
            print(f"    Requirements: {rec['requirements']['cpu']} vCPUs, {rec['requirements']['memory']/1024/1024/1024:.1f}GB RAM")
    
    # Show node status summary
    print("\nNode Status:")
    for node, status in recommendations['node_status'].items():
        cpu = f"{status['cpu_usage']*100:.1f}%"
        memory = f"{status['memory_usage']*100:.1f}%"
        status_text = "OVERLOADED" if status['status'] == 'overloaded' else "Normal"
        
        print(f"  {node:<15} {status_text:<10} CPU: {cpu:<8} Memory: {memory:<8}")

def show_proxmox_config_status(proxmox_api):
    """Display Proxmox configuration status"""
    print("\nProxmox Configuration Status:")
    
    # Check configuration status
    status = proxmox_api.check_proxmox_config_status()
    
    if not status:
        print("  Error: Unable to check Proxmox configuration status")
        return
    
    # Print configuration status
    for component, configured in status.items():
        status_text = "Configured" if configured else "Not Configured"
        print(f"  {component.capitalize():<15}: {status_text}")
    
    # Check HA configuration
    ha_status = proxmox_api.check_ha_config()
    if ha_status:
        print("\nHA Groups:")
        ha_groups = proxmox_api.get("cluster/ha/groups") or []
        if ha_groups:
            for group in ha_groups:
                print(f"  {group.get('group', 'Unknown')}: {group.get('nodes', 'No nodes')}")
        else:
            print("  No HA groups configured")
        
        print("\nHA Resources:")
        ha_resources = proxmox_api.get("cluster/ha/resources") or []
        if ha_resources:
            for resource in ha_resources:
                sid = resource.get('sid', 'Unknown')
                state = resource.get('state', 'Unknown')
                group = resource.get('group', 'No group')
                print(f"  {sid}: State={state}, Group={group}")
        else:
            print("  No HA resources configured")
    
    # Check migration settings
    cluster_options = proxmox_api.get("cluster/options")
    if cluster_options:
        migration_type = cluster_options.get('migration') or 'Not configured'
        print(f"\nMigration Type: {migration_type}")

def main():
    """Main function"""
    args = parse_arguments()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("load_balancer.log"),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger("ProxmoxLoadBalancer")
    
    # Get password if not provided
    password = args.password
    if not password and not args.status:
        import getpass
        password = getpass.getpass("Proxmox password: ")
    
    # Initialize API
    api = ProxmoxAPI(
        host=args.host,
        user=args.user,
        password=password,
        realm=args.realm,
        verify_ssl=args.verify_ssl,
        port=args.port
    )
    
    # Test API connection
    if not api.login():
        logger.error("Failed to authenticate with Proxmox API")
        sys.exit(1)
    
    # Initialize Load Balancer
    load_balancer = LoadBalancer(api, config_file=args.config_file)
    
    # Update configuration from command line arguments
    if (args.config or args.exclude_vm or args.exclude_node or 
        args.auto_configure_proxmox is not None or args.configure_ha is not None or 
        args.configure_migration is not None or args.ha_group_name or args.critical_vm):
        load_balancer.config = update_config_from_args(load_balancer.config, args)
    
    # Handle different operation modes
    if args.config:
        # Interactive configuration
        new_config = config_interactive(load_balancer)
        if new_config:
            load_balancer.config = new_config
            if args.save_config:
                load_balancer.save_config(args.config_file)
                print(f"Configuration saved to {args.config_file}")
    
    elif args.status:
        # Show cluster status
        show_cluster_status(load_balancer)
    
    elif args.recommendations:
        # Show recommendations
        show_recommendations(load_balancer)
    
    elif args.check_proxmox:
        # Show Proxmox configuration status
        show_proxmox_config_status(api)
    
    elif args.configure_proxmox:
        # Configure Proxmox
        print("Configuring Proxmox for load balancing...")
        result = load_balancer.check_and_configure_proxmox()
        
        if result.get("status") == "configured":
            print("Proxmox has been configured successfully for load balancing.")
            print(f"Details: {result.get('details', {})}")
        elif result.get("status") == "already_configured":
            print("Proxmox is already properly configured for load balancing.")
        elif result.get("status") == "skipped":
            print("Configuration check was skipped (already checked recently).")
            print("Use --force-configure to configure anyway.")
        else:
            print(f"Error configuring Proxmox: {result.get('message', 'Unknown error')}")
    
    elif args.update_critical_vms:
        # Update critical VMs list
        print("Identifying critical VMs for HA...")
        result = load_balancer.update_critical_vms()
        
        if result:
            print("Critical VMs have been updated in configuration.")
            critical_vms = load_balancer.config.get("proxmox_config", {}).get("critical_vms", [])
            if critical_vms:
                print("Critical VMs:")
                for vm_id in critical_vms:
                    print(f"  VM {vm_id}")
            else:
                print("No critical VMs identified.")
        else:
            print("Failed to update critical VMs. Check the log for details.")
    
    elif args.once:
        # Run balance once
        print("Running cluster balance...")
        result = load_balancer.balance_cluster()
        print("Balance complete.")
        if not result:
            print("No migrations were performed.")
    
    elif args.daemon:
        # Run as daemon
        print(f"Starting load balancer daemon (check interval: {load_balancer.config['check_interval']} seconds)")
        print("Press Ctrl+C to stop")
        
        try:
            load_balancer.start()
            
            # Keep main thread alive
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\nStopping load balancer...")
            load_balancer.stop()
            print("Load balancer stopped.")

if __name__ == "__main__":
    main()