#!/usr/bin/env python3
"""
Proxmox Load Balancer - Documentation

OVERVIEW:
---------
This system provides an intelligent load balancer for Proxmox virtual environments,
automatically migrating VMs between nodes to optimize resource utilization and ensure
high availability.

COMPONENTS:
-----------
1. proxmox_api.py - API wrapper for Proxmox VE
2. node_selector.py - Intelligent node selection for migrations
3. load_balancer.py - Core load balancer implementation
4. proxmox_load_balancer.py - Command-line interface
5. load_balancer_api.py - REST API for integration with other systems

FEATURES:
---------
- Automatic detection of overloaded and underloaded nodes
- Intelligent VM migration decisions based on resource usage patterns
- VM affinity detection to keep related VMs together
- Critical VM identification for high-availability
- Predictive analytics to prevent future resource constraints
- REST API for integration with monitoring systems
- Detailed recommendations with impact analysis

CONFIGURATION:
--------------
The load balancer can be configured via the config file (default: load_balancer_config.json)
or through command-line arguments and the API.

Key configuration options:
- check_interval: Seconds between balance checks
- high_load_threshold: CPU/Memory usage threshold for high load
- low_load_threshold: CPU/Memory usage threshold for low load
- resource_weights: Weights for different resource types (cpu, memory, disk, network)
- vm_exclusions: List of VMs to exclude from balancing
- node_exclusions: List of nodes to exclude from balancing
- vm_groups: Groups of VMs that should stay together
- proxmox_config: Configuration for Proxmox integration (HA, migration settings)

USAGE:
------
Command-line:
    python proxmox_load_balancer.py --host <proxmox_host> --user <username> --password <password> [OPTIONS]

API:
    python load_balancer_api.py --proxmox-host <proxmox_host> --proxmox-user <username> --proxmox-password <password> --api-key <key>

For more details, see README.md
"""

# This is a documentation file only - no code is executed