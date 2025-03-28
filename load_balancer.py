#!/usr/bin/env python3
import time
import logging
import threading
import json
import os
from datetime import datetime, timedelta
from proxmox_api import ProxmoxAPI
from node_selector import NodeSelector

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

class LoadBalancer:
    """Intelligent Load Balancer for Proxmox clusters"""
    
    def __init__(self, proxmox_api, config_file=None):
        """
        Initialize the Load Balancer
        
        Args:
            proxmox_api (ProxmoxAPI): Instance of the ProxmoxAPI class
            config_file (str, optional): Path to configuration file
        """
        self.proxmox_api = proxmox_api
        self.node_selector = NodeSelector(proxmox_api)
        self.running = False
        self.thread = None
        self.load_config(config_file)
        self.migration_history = []
        self.last_balance_time = {}  # Track when each VM was last balanced
        self.vm_performance_history = {}  # Track VM performance over time
        
        # Check if Proxmox auto-configuration is enabled
        if self.config.get("auto_configure_proxmox", True):
            self.check_and_configure_proxmox()
        
    def load_config(self, config_file=None):
        """
        Load configuration from file or use defaults
        
        Args:
            config_file (str, optional): Path to configuration file
        """
        # Default configuration
        self.config = {
            "check_interval": 300,  # seconds between balance checks
            "high_load_threshold": 0.8,  # CPU or memory usage above this is considered high
            "low_load_threshold": 0.2,  # CPU or memory usage below this is considered low
            "min_balance_interval": 3600,  # minimum seconds between migrations for the same VM
            "max_parallel_migrations": 2,  # maximum concurrent migrations
            "migrate_high_load": True,  # whether to migrate VMs from high-load nodes
            "migrate_to_low_load": True,  # whether to prefer low-load nodes
            "resource_weights": {  # Weights for resource importance
                "cpu": 0.4,
                "memory": 0.4,
                "disk": 0.15,
                "network": 0.05
            },
            "vm_exclusions": [],  # List of VM IDs to exclude from balancing
            "node_exclusions": [],  # List of node names to exclude from balancing
            "consider_affinity": True,  # Whether to consider VM-to-VM affinity
            "vm_groups": {},  # Groups of VMs that should stay together
            "consider_time_of_day": True,  # Whether to consider time of day for migrations
            "off_hours": {  # Hours considered "off hours" for migrations
                "start": 22,  # 10 PM
                "end": 6  # 6 AM
            },
            "learning_enabled": True,  # Whether to learn from migration outcomes
            "ai_features": {
                "prediction_enabled": True,
                "vm_profiling": True,
                "anomaly_detection": True
            },
            # Nouvelles options pour la configuration automatique de Proxmox
            "auto_configure_proxmox": True,  # Whether to auto-configure Proxmox
            "proxmox_config": {
                "configure_ha": True,  # Whether to configure HA
                "configure_migration": True,  # Whether to configure migration settings
                "ha_group_name": "lb-ha-group",  # Name for the HA group
                "critical_vms": [],  # List of VM IDs considered critical for HA
                "check_proxmox_config_interval": 86400  # Check Proxmox config once a day
            },
            "last_proxmox_config_check": 0  # Timestamp of last Proxmox config check
        }
        
        # Override with file config if provided
        if config_file and os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    file_config = json.load(f)
                    # Update config with file values
                    for key, value in file_config.items():
                        if key == "resource_weights" and isinstance(value, dict):
                            # Ensure weights sum to 1
                            total = sum(value.values())
                            if abs(total - 1.0) > 0.01:
                                logger.warning("Resource weights don't sum to 1.0, normalizing")
                                self.config["resource_weights"] = {k: v/total for k, v in value.items()}
                            else:
                                self.config["resource_weights"] = value
                        else:
                            self.config[key] = value
                logger.info(f"Loaded configuration from {config_file}")
            except Exception as e:
                logger.error(f"Error loading config from {config_file}: {str(e)}")
        
        # Update node selector weights
        self.node_selector.set_weights(self.config["resource_weights"])
        
    def save_config(self, config_file="load_balancer_config.json"):
        """
        Save current configuration to file
        
        Args:
            config_file (str): Path to save configuration file
        """
        try:
            with open(config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info(f"Configuration saved to {config_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving config to {config_file}: {str(e)}")
            return False
    
    def start(self):
        """Start the load balancer in a separate thread"""
        if self.running:
            logger.warning("Load balancer is already running")
            return False
            
        self.running = True
        self.thread = threading.Thread(target=self._balancing_loop)
        self.thread.daemon = True
        self.thread.start()
        logger.info("Load balancer started")
        return True
        
    def stop(self):
        """Stop the load balancer"""
        if not self.running:
            logger.warning("Load balancer is not running")
            return False
            
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Load balancer stopped")
        return True
        
    def _balancing_loop(self):
        """Main loop for periodic load balancing"""
        last_resource_update = 0
        resource_update_interval = 60  # Update resources every minute
        
        while self.running:
            try:
                # Check and monitor migration status
                self.monitor_migrations()
                
                # Update resource usage periodically
                current_time = time.time()
                if current_time - last_resource_update > resource_update_interval:
                    self.periodic_update_resources()
                    last_resource_update = current_time
                
                # Check if balance is needed
                self.balance_cluster()
                
            except Exception as e:
                logger.error(f"Error in balancing loop: {str(e)}")
                
            # Sleep for check interval
            for _ in range(self.config["check_interval"]):
                if not self.running:
                    break
                time.sleep(1)
    
    def _is_migration_allowed(self, vm_id):
        """
        Check if migration is allowed for a VM
        
        Args:
            vm_id (int): VM ID
            
        Returns:
            bool: Whether migration is allowed
        """
        # Check exclusions
        if str(vm_id) in self.config["vm_exclusions"] or int(vm_id) in self.config["vm_exclusions"]:
            return False
            
        # Check last balance time
        if vm_id in self.last_balance_time:
            time_since_last = time.time() - self.last_balance_time[vm_id]
            if time_since_last < self.config["min_balance_interval"]:
                return False
        
        # Check time of day restrictions
        if self.config["consider_time_of_day"]:
            current_hour = datetime.now().hour
            off_hours_start = self.config["off_hours"]["start"]
            off_hours_end = self.config["off_hours"]["end"]
            
            # Check if current time is outside off hours
            if off_hours_start < off_hours_end:
                # Simple case: off hours within the same day
                is_off_hours = off_hours_start <= current_hour < off_hours_end
            else:
                # Complex case: off hours span midnight
                is_off_hours = current_hour >= off_hours_start or current_hour < off_hours_end
            
            # Only allow migrations during off hours
            if not is_off_hours:
                return False
        
        return True
    
    def _get_vm_requirements(self, vm_info):
        """
        Extract resource requirements from VM info
        
        Args:
            vm_info (dict): VM information
            
        Returns:
            dict: Resource requirements
        """
        return {
            'cpu': vm_info.get('maxcpu', vm_info.get('cpus', 1)),
            'memory': vm_info.get('maxmem', 1024 * 1024 * 1024),  # Default to 1GB if not found
            'disk': vm_info.get('maxdisk', 10 * 1024 * 1024 * 1024)  # Default to 10GB if not found
        }
    
    def detect_overloaded_nodes(self):
        """
        Detect nodes with high resource utilization
        
        Returns:
            list: Names of overloaded nodes
        """
        overloaded_nodes = []
        nodes_usage = self.proxmox_api.get_resource_usage()
        
        if not nodes_usage:
            return []
            
        for node in nodes_usage:
            if node['status'] != 'online':
                continue
                
            # Check if node is in exclusion list
            if node['name'] in self.config["node_exclusions"]:
                continue
                
            # Check CPU and memory usage against high load threshold
            cpu_usage = node['cpu']['usage']
            memory_usage = node['memory']['used'] / node['memory']['total'] if node['memory']['total'] > 0 else 0
            
            if cpu_usage > self.config["high_load_threshold"] or memory_usage > self.config["high_load_threshold"]:
                overloaded_nodes.append(node['name'])
                
        return overloaded_nodes
    
    def detect_underloaded_nodes(self):
        """
        Detect nodes with low resource utilization
        
        Returns:
            list: Names of underloaded nodes
        """
        underloaded_nodes = []
        nodes_usage = self.proxmox_api.get_resource_usage()
        
        if not nodes_usage:
            return []
            
        for node in nodes_usage:
            if node['status'] != 'online':
                continue
                
            # Check if node is in exclusion list
            if node['name'] in self.config["node_exclusions"]:
                continue
                
            # Check CPU and memory usage against low load threshold
            cpu_usage = node['cpu']['usage']
            memory_usage = node['memory']['used'] / node['memory']['total'] if node['memory']['total'] > 0 else 0
            
            if cpu_usage < self.config["low_load_threshold"] and memory_usage < self.config["low_load_threshold"]:
                underloaded_nodes.append(node['name'])
                
        return underloaded_nodes
    
    def identify_vms_to_migrate(self, node_name, count=2):
        """
        Identify VMs on a node that are good candidates for migration
        
        Args:
            node_name (str): Name of the node
            count (int): Maximum number of VMs to select
            
        Returns:
            list: List of VM IDs to migrate
        """
        vms = self.proxmox_api.get_node_vms(node_name)
        if not vms:
            return []
            
        # Filter out VMs that shouldn't be migrated
        eligible_vms = []
        for vm in vms:
            vm_id = vm['vmid']
            
            # Skip if VM is not running
            if vm['status'] != 'running':
                continue
                
            # Check if migration is allowed
            if not self._is_migration_allowed(vm_id):
                continue
                
            # Add to eligible list
            eligible_vms.append(vm)
            
        if not eligible_vms:
            return []
            
        # Sort VMs by resource usage (highest first)
        # This is a simple heuristic - we migrate the most resource-intensive VMs first
        sorted_vms = sorted(eligible_vms, key=lambda x: x.get('cpu', 0), reverse=True)
        
        # Return VM IDs
        return [vm['vmid'] for vm in sorted_vms[:count]]
    
    def balance_cluster(self):
        """
        Perform load balancing across the cluster
        
        Returns:
            bool: Whether any migrations were performed
        """
        logger.info("Starting cluster balance check")
        
        # Check for migrations in progress
        current_migrations = self.proxmox_api.get("cluster/tasks?running=1") or []
        migration_tasks = [task for task in current_migrations if task.get('type') == 'qmigrate']
        
        if len(migration_tasks) >= self.config["max_parallel_migrations"]:
            logger.info(f"Skipping balance: {len(migration_tasks)} migrations already in progress")
            return False
        
        # Step 1: Detect overloaded nodes
        overloaded_nodes = self.detect_overloaded_nodes()
        logger.info(f"Overloaded nodes: {overloaded_nodes}")
        
        # Step 2: Detect underloaded nodes
        underloaded_nodes = self.detect_underloaded_nodes()
        logger.info(f"Underloaded nodes: {underloaded_nodes}")
        
        # Step 3: For each overloaded node, migrate VMs to less loaded nodes
        migrations_performed = 0
        migrations_allowed = self.config["max_parallel_migrations"] - len(migration_tasks)
        
        # Define a rebalance strategy
        strategies = []
        
        # Strategy 1: Migrate from overloaded to underloaded nodes (if enabled)
        if self.config["migrate_high_load"] and overloaded_nodes and migrations_allowed > 0:
            strategies.append(("high_to_low", overloaded_nodes, underloaded_nodes))
        
        # Strategy 2: Distribute load evenly if no overloaded nodes but underloaded nodes exist
        if not overloaded_nodes and underloaded_nodes and migrations_allowed > 0:
            # Find nodes with normal load but not underloaded
            all_nodes = [n['node'] for n in self.proxmox_api.get_nodes() if 
                        n['status'] == 'online' and 
                        n['node'] not in underloaded_nodes and
                        n['node'] not in self.config["node_exclusions"]]
            
            if all_nodes:
                strategies.append(("distribution", all_nodes, underloaded_nodes))
        
        # Strategy 3: Implement VM affinity if enabled
        if self.config["consider_affinity"] and self.config["vm_groups"] and migrations_allowed > 0:
            # Find VM groups that are split across nodes and try to consolidate them
            for group_name, group_vms in self.config["vm_groups"].items():
                if len(group_vms) < 2:
                    continue
                    
                # Find current location of these VMs
                vm_locations = {}
                for vm_id in group_vms:
                    for node in self.proxmox_api.get_nodes():
                        if node['status'] != 'online':
                            continue
                            
                        node_vms = self.proxmox_api.get_node_vms(node['node']) or []
                        if any(vm['vmid'] == int(vm_id) for vm in node_vms):
                            vm_locations[vm_id] = node['node']
                            break
                
                # If VMs are split across nodes, add a consolidation strategy
                if len(set(vm_locations.values())) > 1:
                    # Find the node with most VMs from this group
                    node_counts = {}
                    for node in vm_locations.values():
                        node_counts[node] = node_counts.get(node, 0) + 1
                    
                    target_node = max(node_counts.items(), key=lambda x: x[1])[0]
                    source_nodes = [node for node in vm_locations.values() if node != target_node]
                    
                    strategies.append(("affinity", source_nodes, [target_node]))
        
        # Execute each strategy in order
        for strategy_name, source_nodes, target_nodes in strategies:
            if migrations_performed >= migrations_allowed:
                break
                
            logger.info(f"Executing {strategy_name} migration strategy")
            
            for source_node in source_nodes:
                if migrations_performed >= migrations_allowed:
                    break
                    
                # Find VMs to migrate
                vms_to_migrate = self.identify_vms_to_migrate(source_node, 
                                                            count=migrations_allowed - migrations_performed)
                
                for vm_id in vms_to_migrate:
                    # Get VM info to determine requirements
                    vm_status = self.proxmox_api.get_vm_status(source_node, vm_id)
                    if not vm_status:
                        continue
                        
                    vm_requirements = self._get_vm_requirements(vm_status)
                    
                    # Find best destination node
                    excluded_nodes = [source_node] + self.config["node_exclusions"]
                    
                    # For affinity strategies, restrict to the target node
                    if strategy_name == "affinity":
                        best_node = None
                        for node in target_nodes:
                            if node not in excluded_nodes:
                                # Check if this node can accept the VM
                                node_score = self.node_selector.calculate_node_score(node, vm_requirements)
                                if node_score < float('inf'):
                                    best_node = node
                                    break
                    # For other strategies
                    elif target_nodes:
                        # Try to find a good target node from our target list first
                        best_node = None
                        for node in target_nodes:
                            if node not in excluded_nodes:
                                # Check if this node can accept the VM
                                node_score = self.node_selector.calculate_node_score(node, vm_requirements)
                                if node_score < float('inf'):
                                    best_node = node
                                    break
                        
                        # If no suitable node found in target list, try any node
                        if not best_node:
                            best_node = self.node_selector.select_best_node(vm_requirements, excluded_nodes)
                    else:
                        # Just find the best node regardless of load
                        best_node = self.node_selector.select_best_node(vm_requirements, excluded_nodes)
                    
                    if best_node:
                        # Apply VM-specific options if migrating a VM in a VM group
                        vm_options = {}
                        is_in_group = False
                        for group_name, group_vms in self.config["vm_groups"].items():
                            if str(vm_id) in group_vms or int(vm_id) in group_vms:
                                is_in_group = True
                                # Priority migrations for VMs in groups
                                vm_options = {"priority": "high"}
                                break
                        
                        # Perform migration
                        logger.info(f"Migrating VM {vm_id} from {source_node} to {best_node} (strategy: {strategy_name})")
                        
                        # Set online migration depending on VM status
                        online = vm_status.get('status') == 'running'
                        
                        result = self.proxmox_api.migrate_vm(source_node, vm_id, best_node, online=online)
                        
                        if result:
                            logger.info(f"Migration of VM {vm_id} initiated successfully")
                            self.last_balance_time[vm_id] = time.time()
                            
                            # Record migration for learning
                            migration_record = {
                                'vm_id': vm_id,
                                'source_node': source_node,
                                'target_node': best_node,
                                'timestamp': time.time(),
                                'reason': strategy_name,
                                'requirements': vm_requirements,
                                'vm_name': vm_status.get('name', f'VM-{vm_id}'),
                                'result': 'initiated'
                            }
                            self.migration_history.append(migration_record)
                            
                            # Add to VM performance history
                            if vm_id not in self.vm_performance_history:
                                self.vm_performance_history[vm_id] = []
                            
                            self.vm_performance_history[vm_id].append({
                                'timestamp': time.time(),
                                'cpu': vm_status.get('cpu', 0),
                                'memory_used': vm_status.get('mem', 0),
                                'node': source_node
                            })
                            
                            migrations_performed += 1
                            if migrations_performed >= migrations_allowed:
                                break
                        else:
                            logger.error(f"Failed to migrate VM {vm_id} from {source_node} to {best_node}")
                
                if migrations_performed >= migrations_allowed:
                    break
        
        # Check if we need to update critical VMs list
        if self.config.get("auto_identify_critical_vms", False):
            now = time.time()
            last_update = self.config.get("last_critical_vms_update", 0)
            if now - last_update > 86400:  # Update once a day
                logger.info("Auto-updating critical VMs list")
                self.update_critical_vms()
                self.config["last_critical_vms_update"] = now
                self.save_config()
        
        # Learn from migrations if enabled
        if self.config["learning_enabled"] and self.migration_history:
            self.learn_from_migrations()
        
        # Return True if any migrations were performed
        return migrations_performed > 0
    
    def get_status(self):
        """
        Get current status of the load balancer
        
        Returns:
            dict: Status information
        """
        return {
            'running': self.running,
            'config': self.config,
            'migration_history': self.migration_history[-10:],  # Last 10 migrations
            'overloaded_nodes': self.detect_overloaded_nodes(),
            'underloaded_nodes': self.detect_underloaded_nodes()
        }
    
    def learn_from_migrations(self):
        """Learn from past migrations to improve future decisions"""
        if not self.config["learning_enabled"] or not self.migration_history:
            return
            
        # This is a simple implementation, a more sophisticated approach 
        # would use machine learning to analyze patterns and outcomes
        
        # Analyze recent migrations for patterns in successful vs failed migrations
        recent_migrations = self.migration_history[-50:]  # Look at last 50 migrations
        
        # Group by source and target nodes
        node_pairs = {}
        for migration in recent_migrations:
            source = migration['source_node']
            target = migration['target_node']
            key = f"{source}:{target}"
            
            if key not in node_pairs:
                node_pairs[key] = {'count': 0, 'success': 0}
                
            node_pairs[key]['count'] += 1
            # We would need to track migration success/failure in a real implementation
            # Here we'll assume all migrations were successful
            node_pairs[key]['success'] += 1
        
        # Calculate success rates
        for pair, stats in node_pairs.items():
            stats['success_rate'] = stats['success'] / stats['count'] if stats['count'] > 0 else 0
            
        # Use this information to adjust weights or thresholds
        # This is just a placeholder for a more sophisticated implementation
        
        logger.info("Learning from migration history completed")
    
    def get_recommendations(self):
        """
        Get recommendations for manual load balancing
        
        Returns:
            dict: Recommendations for migrations
        """
        recommendations = {
            'migrations': [],
            'node_status': {}
        }
        
        # Get overloaded nodes
        overloaded_nodes = self.detect_overloaded_nodes()
        
        # For each overloaded node, recommend VM migrations
        for node in overloaded_nodes:
            vms_to_migrate = self.identify_vms_to_migrate(node, count=3)
            
            for vm_id in vms_to_migrate:
                # Get VM details
                vm_status = self.proxmox_api.get_vm_status(node, vm_id)
                if not vm_status:
                    continue
                    
                vm_requirements = self._get_vm_requirements(vm_status)
                
                # Get top 3 recommended target nodes
                excluded_nodes = [node] + self.config["node_exclusions"]
                target_nodes = self.node_selector.get_node_recommendations(count=3, vm_requirements=vm_requirements)
                
                # Filter out excluded nodes
                target_nodes = [n for n in target_nodes if n not in excluded_nodes]
                
                if target_nodes:
                    recommendations['migrations'].append({
                        'vm_id': vm_id,
                        'source_node': node,
                        'target_nodes': target_nodes,
                        'vm_name': vm_status.get('name', f'VM-{vm_id}'),
                        'requirements': vm_requirements
                    })
        
        # Get status for all nodes
        nodes_usage = self.proxmox_api.get_resource_usage()
        if nodes_usage:
            for node in nodes_usage:
                if node['status'] == 'online':
                    recommendations['node_status'][node['name']] = {
                        'cpu_usage': node['cpu']['usage'],
                        'memory_usage': node['memory']['used'] / node['memory']['total'] if node['memory']['total'] > 0 else 0,
                        'status': 'overloaded' if node['name'] in overloaded_nodes else 'normal'
                    }
        
        return recommendations
    
    def check_and_configure_proxmox(self):
        """
        Check if Proxmox is correctly configured for load balancing and configure it if needed
        
        Returns:
            dict: Status of Proxmox configuration
        """
        # Check if we should recheck Proxmox configuration
        current_time = time.time()
        if (current_time - self.config.get("last_proxmox_config_check", 0) < 
            self.config["proxmox_config"].get("check_proxmox_config_interval", 86400)):
            logger.debug("Skipping Proxmox config check, already checked recently")
            return {"status": "skipped", "message": "Already checked recently"}
        
        logger.info("Checking Proxmox configuration for load balancing")
        
        # Check current configuration status
        config_status = self.proxmox_api.check_proxmox_config_status()
        logger.info(f"Current Proxmox configuration status: {config_status}")
        
        # Determine if configuration is needed
        needs_config = False
        for component, status in config_status.items():
            if not status and component in ['ha', 'migration']:
                needs_config = True
                break
        
        if not needs_config:
            logger.info("Proxmox is already properly configured for load balancing")
            self.config["last_proxmox_config_check"] = current_time
            self.save_config()
            return {"status": "already_configured", "message": "Proxmox is already properly configured"}
        
        # Configure Proxmox automatically
        logger.info("Configuring Proxmox automatically for optimal load balancing")
        
        try:
            # Get configuration options
            proxmox_config = self.config["proxmox_config"]
            configure_ha = proxmox_config.get("configure_ha", True)
            configure_migration = proxmox_config.get("configure_migration", True)
            ha_group_name = proxmox_config.get("ha_group_name", "lb-ha-group")
            
            # Perform auto-configuration
            config_result = self.proxmox_api.auto_configure_proxmox(
                configure_ha=configure_ha,
                configure_migration=configure_migration,
                ha_group_name=ha_group_name
            )
            
            # Try to enable HA for critical VMs
            if configure_ha and "critical_vms" in proxmox_config and proxmox_config["critical_vms"]:
                for vm_id in proxmox_config["critical_vms"]:
                    # First, find which node this VM is on
                    nodes = self.proxmox_api.get_nodes()
                    vm_node = None
                    for node in nodes:
                        if node['status'] != 'online':
                            continue
                        node_vms = self.proxmox_api.get_node_vms(node['node']) or []
                        if any(vm['vmid'] == int(vm_id) for vm in node_vms):
                            vm_node = node['node']
                            break
                    
                    if vm_node:
                        logger.info(f"Enabling HA for critical VM {vm_id} on node {vm_node}")
                        self.proxmox_api.enable_vm_ha(vm_node, vm_id, ha_group_name)
            
            # Update last check time
            self.config["last_proxmox_config_check"] = current_time
            self.save_config()
            
            logger.info(f"Proxmox auto-configuration completed: {config_result}")
            return {
                "status": "configured", 
                "message": "Proxmox configured successfully for load balancing",
                "details": config_result
            }
            
        except Exception as e:
            logger.error(f"Failed to auto-configure Proxmox: {str(e)}")
            return {"status": "error", "message": f"Error configuring Proxmox: {str(e)}"}
    
    def identify_critical_vms(self, max_count=5):
        """
        Identify critical VMs that should have HA enabled
        
        Args:
            max_count (int): Maximum number of VMs to identify
            
        Returns:
            list: List of VM IDs identified as critical
        """
        # Get all VMs in the cluster
        nodes = self.proxmox_api.get_nodes()
        all_vms = []
        for node in nodes:
            if node['status'] != 'online':
                continue
            node_vms = self.proxmox_api.get_node_vms(node['node']) or []
            for vm in node_vms:
                if vm['status'] == 'running':
                    vm['node'] = node['node']
                    all_vms.append(vm)
        
        if not all_vms:
            return []
        
        # Use a simple heuristic to identify critical VMs:
        # - VMs with more resources allocated
        # - VMs with longer uptime
        # A more sophisticated approach would involve analyzing historical
        # workloads, dependencies, and business impact
        
        # Score VMs based on resources and uptime
        scored_vms = []
        for vm in all_vms:
            # Get VM details
            vm_status = self.proxmox_api.get_vm_status(vm['node'], vm['vmid'])
            if not vm_status:
                continue
            
            # Calculate a simple criticality score
            cpu_score = vm_status.get('cpus', 1) * 10
            mem_score = vm_status.get('maxmem', 1024 * 1024 * 1024) / (1024 * 1024 * 1024) * 5  # Convert to GB
            uptime_score = vm_status.get('uptime', 0) / (86400 * 7) * 20  # Normalize to weeks
            
            # Cap uptime score
            uptime_score = min(uptime_score, 100)
            
            total_score = cpu_score + mem_score + uptime_score
            scored_vms.append({
                'vmid': vm['vmid'],
                'name': vm.get('name', f"VM-{vm['vmid']}"),
                'score': total_score
            })
        
        # Sort by score (descending)
        scored_vms.sort(key=lambda x: x['score'], reverse=True)
        
        # Return top N VM IDs
        return [vm['vmid'] for vm in scored_vms[:max_count]]
    
    def update_critical_vms(self):
        """
        Update the list of critical VMs in the configuration
        
        Returns:
            bool: Whether the update was successful
        """
        try:
            critical_vms = self.identify_critical_vms()
            logger.info(f"Identified critical VMs: {critical_vms}")
            
            self.config["proxmox_config"]["critical_vms"] = critical_vms
            self.save_config()
            return True
            
        except Exception as e:
            logger.error(f"Failed to update critical VMs: {str(e)}")
            return False
            
    def detect_vm_affinity_groups(self):
        """
        Detect groups of VMs that should stay together based on network traffic and resource patterns
        
        Returns:
            dict: Dictionary of VM groups
        """
        # Get all VMs in the cluster
        nodes = self.proxmox_api.get_nodes()
        all_vms = []
        for node in nodes:
            if node['status'] != 'online':
                continue
            node_vms = self.proxmox_api.get_node_vms(node['node']) or []
            for vm in node_vms:
                vm['node'] = node['node']
                all_vms.append(vm)
        
        if not all_vms:
            return {}
            
        # Group VMs by name patterns
        # This is a simple approach. A more sophisticated approach would
        # analyze network traffic between VMs
        groups = {}
        
        # Pattern-based grouping
        for vm in all_vms:
            name = vm.get('name', '')
            if not name:
                continue
                
            # Extract potential group indicators from name
            # Common patterns: app-db1/app-web1, service-node1/service-node2, etc.
            parts = name.split('-')
            if len(parts) >= 2:
                prefix = parts[0].lower()
                # Only consider prefixes with at least 2 characters
                if len(prefix) >= 2:
                    if prefix not in groups:
                        groups[prefix] = []
                    groups[prefix].append(vm['vmid'])
        
        # Only keep groups with at least 2 VMs
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        
        # For VMs with performance history, check for correlated resource usage
        if self.vm_performance_history:
            correlated_groups = self._detect_correlated_vm_groups()
            
            # Merge with pattern-based groups
            for group_name, vm_ids in correlated_groups.items():
                if group_name not in groups:
                    groups[group_name] = vm_ids
                else:
                    # Add any VMs that aren't already in the group
                    for vm_id in vm_ids:
                        if vm_id not in groups[group_name]:
                            groups[group_name].append(vm_id)
        
        return groups
        
    def _detect_correlated_vm_groups(self):
        """
        Detect VMs with correlated resource usage patterns
        
        Returns:
            dict: Dictionary of correlated VM groups
        """
        # This is a simplified implementation
        # A more sophisticated approach would use statistical correlation
        correlated_groups = {}
        group_counter = 0
        
        # We need at least a few VMs with history
        if len(self.vm_performance_history) < 2:
            return correlated_groups
            
        # Create a VM correlation matrix
        correlated_pairs = []
        
        # Compare VM histories
        vm_ids = list(self.vm_performance_history.keys())
        for i in range(len(vm_ids)):
            vm1 = vm_ids[i]
            history1 = self.vm_performance_history[vm1]
            
            if len(history1) < 5:  # Need enough data points
                continue
                
            for j in range(i + 1, len(vm_ids)):
                vm2 = vm_ids[j]
                history2 = self.vm_performance_history[vm2]
                
                if len(history2) < 5:  # Need enough data points
                    continue
                    
                # Simple correlation: check if CPU usage patterns are similar
                # This is a very simplistic approach
                correlation = self._calculate_simple_correlation(history1, history2)
                
                if correlation > 0.7:  # High correlation threshold
                    correlated_pairs.append((vm1, vm2, correlation))
        
        # Convert correlated pairs to groups
        if correlated_pairs:
            # Sort by correlation strength
            correlated_pairs.sort(key=lambda x: x[2], reverse=True)
            
            # Build groups using a simple algorithm
            grouped_vms = set()
            for vm1, vm2, _ in correlated_pairs:
                # Check if either VM is already in a group
                found_group = False
                for group_name, vm_ids in correlated_groups.items():
                    if vm1 in vm_ids or vm2 in vm_ids:
                        # Add the other VM to the group
                        if vm1 not in vm_ids:
                            vm_ids.append(vm1)
                        if vm2 not in vm_ids:
                            vm_ids.append(vm2)
                        found_group = True
                        break
                
                # If neither VM is in a group, create a new group
                if not found_group and (vm1 not in grouped_vms or vm2 not in grouped_vms):
                    group_counter += 1
                    group_name = f"correlated_group_{group_counter}"
                    correlated_groups[group_name] = [vm1, vm2]
                    grouped_vms.add(vm1)
                    grouped_vms.add(vm2)
        
        return correlated_groups
    
    def _calculate_simple_correlation(self, history1, history2):
        """
        Calculate a simple correlation between two VM usage histories
        
        Args:
            history1 (list): Usage history for VM 1
            history2 (list): Usage history for VM 2
            
        Returns:
            float: Correlation coefficient (-1 to 1)
        """
        # Extract CPU usage from history
        cpu1 = [entry.get('cpu', 0) for entry in history1[-10:]]  # Use last 10 entries
        cpu2 = [entry.get('cpu', 0) for entry in history2[-10:]]
        
        # Need at least 3 data points
        if len(cpu1) < 3 or len(cpu2) < 3:
            return 0
            
        # Calculate means
        mean1 = sum(cpu1) / len(cpu1)
        mean2 = sum(cpu2) / len(cpu2)
        
        # Calculate correlation coefficient
        numerator = sum((cpu1[i] - mean1) * (cpu2[i] - mean2) for i in range(min(len(cpu1), len(cpu2))))
        denominator1 = sum((x - mean1) ** 2 for x in cpu1)
        denominator2 = sum((x - mean2) ** 2 for x in cpu2)
        
        # Avoid division by zero
        if denominator1 == 0 or denominator2 == 0:
            return 0
            
        correlation = numerator / ((denominator1 * denominator2) ** 0.5)
        
        return correlation
    
    def update_vm_groups(self):
        """
        Update VM groups based on detected affinity
        
        Returns:
            bool: Whether the update was successful
        """
        try:
            detected_groups = self.detect_vm_affinity_groups()
            logger.info(f"Detected VM affinity groups: {detected_groups}")
            
            # Merge with existing groups
            existing_groups = self.config.get("vm_groups", {})
            
            # For existing groups, keep them if they don't overlap with detected groups
            for group_name, vm_ids in existing_groups.items():
                if group_name not in detected_groups:
                    detected_groups[group_name] = vm_ids
            
            # Update configuration
            self.config["vm_groups"] = detected_groups
            self.save_config()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to update VM groups: {str(e)}")
            return False
            
    def analyze_migration_impact(self, vm_id, source_node, target_node):
        """
        Analyze the potential impact of migrating a VM
        
        Args:
            vm_id (int): VM ID to migrate
            source_node (str): Source node name
            target_node (str): Target node name
            
        Returns:
            dict: Impact analysis
        """
        impact = {
            'performance_impact': 'low',
            'risk_level': 'low',
            'recommended': True,
            'reasons': []
        }
        
        # Get VM status
        vm_status = self.proxmox_api.get_vm_status(source_node, vm_id)
        if not vm_status:
            impact['recommended'] = False
            impact['reasons'].append("Unable to get VM status")
            return impact
        
        # Check if VM has performance history
        if vm_id in self.vm_performance_history and len(self.vm_performance_history[vm_id]) > 3:
            # Check if VM has recently had high resource usage
            recent_history = self.vm_performance_history[vm_id][-3:]
            avg_cpu = sum(entry.get('cpu', 0) for entry in recent_history) / len(recent_history)
            
            if avg_cpu > 0.7:  # High CPU usage
                impact['performance_impact'] = 'medium'
                impact['risk_level'] = 'medium'
                impact['reasons'].append("VM has had high CPU usage recently")
        
        # Check if VM is in an affinity group
        in_group = False
        for group_name, group_vms in self.config.get("vm_groups", {}).items():
            if str(vm_id) in group_vms or int(vm_id) in group_vms:
                in_group = True
                
                # Check if other VMs in the group are on the target node
                group_on_target = False
                for other_vm in group_vms:
                    if other_vm != str(vm_id) and other_vm != int(vm_id):
                        # Check if this VM is on the target node
                        target_vms = self.proxmox_api.get_node_vms(target_node) or []
                        if any(vm['vmid'] == int(other_vm) for vm in target_vms):
                            group_on_target = True
                            break
                
                if group_on_target:
                    impact['reasons'].append(f"VM is part of affinity group '{group_name}' with VMs on target node")
                else:
                    impact['performance_impact'] = 'medium'
                    impact['reasons'].append(f"VM is part of affinity group '{group_name}' but no other group VMs on target node")
                
                break
        
        # Check if target node has enough resources
        target_status = self.proxmox_api.get_node_status(target_node)
        if target_status:
            vm_cpu = vm_status.get('cpus', 1)
            vm_memory = vm_status.get('maxmem', 1024 * 1024 * 1024)
            
            target_free_cpu = target_status.get('cpuinfo', {}).get('cpus', 0) * (1 - target_status.get('cpu', 0))
            target_free_memory = target_status.get('memory', {}).get('free', 0)
            
            cpu_margin = target_free_cpu - vm_cpu
            memory_margin = target_free_memory - vm_memory
            
            if cpu_margin < 1:  # Less than 1 CPU core free
                impact['performance_impact'] = 'high'
                impact['risk_level'] = 'high'
                impact['recommended'] = False
                impact['reasons'].append("Target node may not have enough CPU for this VM")
            
            if memory_margin < 1024 * 1024 * 1024:  # Less than 1GB memory margin
                impact['performance_impact'] = 'high'
                impact['risk_level'] = 'high'
                impact['recommended'] = False
                impact['reasons'].append("Target node may not have enough memory for this VM")
        
        # Check historical migration success
        for migration in self.migration_history:
            if migration['vm_id'] == vm_id and migration['target_node'] == target_node:
                if migration.get('result') == 'success':
                    impact['reasons'].append("VM has been successfully migrated to this node before")
                    break
                elif migration.get('result') == 'failed':
                    impact['risk_level'] = 'high'
                    impact['recommended'] = False
                    impact['reasons'].append("Previous migration of this VM to this node failed")
                    break
        
        return impact
    
    def get_detailed_recommendations(self):
        """
        Get detailed recommendations with impact analysis
        
        Returns:
            dict: Detailed migration recommendations
        """
        base_recommendations = self.get_recommendations()
        detailed_recommendations = {
            'migrations': [],
            'node_status': base_recommendations['node_status']
        }
        
        # Add impact analysis to each recommendation
        for rec in base_recommendations['migrations']:
            vm_id = rec['vm_id']
            source_node = rec['source_node']
            
            # Analyze impact for each target node
            target_analyses = []
            for target_node in rec['target_nodes']:
                impact = self.analyze_migration_impact(vm_id, source_node, target_node)
                target_analyses.append({
                    'node': target_node,
                    'impact_analysis': impact
                })
            
            # Add the detailed recommendation
            detailed_recommendations['migrations'].append({
                'vm_id': vm_id,
                'vm_name': rec['vm_name'],
                'source_node': source_node,
                'target_analyses': target_analyses,
                'requirements': rec['requirements']
            })
        
        return detailed_recommendations
    
    def monitor_migrations(self):
        """
        Monitor ongoing migrations and track their completion status
        
        This method updates the migration_history with success/failure status
        """
        if not self.migration_history:
            return
        
        # Find migrations that were initiated but don't have a result yet
        incomplete_migrations = [m for m in self.migration_history 
                               if m.get('result') == 'initiated']
        
        if not incomplete_migrations:
            return
            
        # Check cluster tasks to find status of each migration
        all_tasks = self.proxmox_api.get("cluster/tasks") or []
        
        for migration in incomplete_migrations:
            vm_id = migration['vm_id']
            source_node = migration['source_node']
            target_node = migration['target_node']
            
            # Find matching task
            matching_tasks = [t for t in all_tasks 
                           if t.get('type') == 'qmigrate' and 
                           str(vm_id) in t.get('id', '') and
                           source_node in t.get('id', '')]
            
            if matching_tasks:
                # Get most recent matching task
                task = sorted(matching_tasks, key=lambda t: t.get('starttime', 0), reverse=True)[0]
                
                if task.get('status') == 'stopped':
                    if task.get('exitstatus') == 'OK':
                        # Migration succeeded
                        logger.info(f"Migration of VM {vm_id} from {source_node} to {target_node} completed successfully")
                        migration['result'] = 'success'
                        migration['completion_time'] = time.time()
                        
                        # Update VM performance tracking
                        if vm_id in self.vm_performance_history:
                            self.vm_performance_history[vm_id].append({
                                'timestamp': time.time(),
                                'node': target_node,
                                'migration_success': True
                            })
                    else:
                        # Migration failed
                        logger.error(f"Migration of VM {vm_id} from {source_node} to {target_node} failed: {task.get('exitstatus')}")
                        migration['result'] = 'failed'
                        migration['completion_time'] = time.time()
                        migration['error'] = task.get('exitstatus', 'Unknown error')
                        
                        # Update VM performance tracking
                        if vm_id in self.vm_performance_history:
                            self.vm_performance_history[vm_id].append({
                                'timestamp': time.time(),
                                'node': source_node,
                                'migration_success': False
                            })
    
    def periodic_update_resources(self):
        """
        Periodically update resource usage patterns for all nodes and VMs
        This allows the load balancer to make better predictions
        """
        try:
            # Update node selector's resource history
            self.node_selector.update_resource_history()
            
            # Update VM resource history
            nodes = self.proxmox_api.get_nodes()
            for node in nodes:
                if node['status'] != 'online':
                    continue
                    
                # Get all VMs on this node
                node_vms = self.proxmox_api.get_node_vms(node['node']) or []
                
                for vm in node_vms:
                    if vm['status'] != 'running':
                        continue
                        
                    vm_id = vm['vmid']
                    vm_status = self.proxmox_api.get_vm_status(node['node'], vm_id)
                    
                    if not vm_status:
                        continue
                        
                    # Create entry in vm_performance_history if it doesn't exist
                    if vm_id not in self.vm_performance_history:
                        self.vm_performance_history[vm_id] = []
                    
                    # Add current performance data
                    self.vm_performance_history[vm_id].append({
                        'timestamp': time.time(),
                        'cpu': vm_status.get('cpu', 0),
                        'memory_used': vm_status.get('mem', 0),
                        'node': node['node']
                    })
                    
                    # Limit history size to avoid memory issues
                    max_history = 100
                    if len(self.vm_performance_history[vm_id]) > max_history:
                        self.vm_performance_history[vm_id] = self.vm_performance_history[vm_id][-max_history:]
            
            # If we have sufficient history, update VM affinity groups
            if self.config.get("auto_update_vm_groups", True) and len(self.vm_performance_history) >= 3:
                now = time.time()
                last_update = self.config.get("last_vm_groups_update", 0)
                if now - last_update > 86400:  # Update once a day
                    logger.info("Auto-updating VM affinity groups")
                    self.update_vm_groups()
                    self.config["last_vm_groups_update"] = now
                    self.save_config()
            
        except Exception as e:
            logger.error(f"Error updating resource data: {str(e)}")
    
    def detect_anomalies(self):
        """
        Detect performance anomalies in the cluster
        
        Returns:
            list: List of detected anomalies
        """
        anomalies = []
        
        if not self.config["ai_features"]["anomaly_detection"]:
            return anomalies
            
        try:
            # Check for sudden resource spikes on nodes
            nodes_usage = self.proxmox_api.get_resource_usage()
            if nodes_usage:
                for node in nodes_usage:
                    node_name = node['name']
                    
                    # Skip offline nodes
                    if node['status'] != 'online':
                        continue
                    
                    # Get resource history for this node
                    cpu_history = self.node_selector.resource_history[node_name]['cpu']
                    memory_history = self.node_selector.resource_history[node_name]['memory']
                    
                    # Need enough history for anomaly detection
                    if len(cpu_history) >= 5 and len(memory_history) >= 5:
                        # Calculate mean and standard deviation for CPU and memory
                        cpu_mean = sum(cpu_history[-5:]) / 5
                        cpu_std = (sum((x - cpu_mean) ** 2 for x in cpu_history[-5:]) / 5) ** 0.5
                        
                        memory_mean = sum(memory_history[-5:]) / 5
                        memory_std = (sum((x - memory_mean) ** 2 for x in memory_history[-5:]) / 5) ** 0.5
                        
                        # Current values
                        current_cpu = node['cpu']['usage']
                        current_memory = node['memory']['used'] / node['memory']['total'] if node['memory']['total'] > 0 else 0
                        
                        # Check if current values are significantly higher than recent history
                        # (more than 3 standard deviations above mean)
                        if cpu_std > 0 and (current_cpu - cpu_mean) / cpu_std > 3:
                            anomalies.append({
                                'type': 'node_cpu_spike',
                                'node': node_name,
                                'value': current_cpu,
                                'mean': cpu_mean,
                                'std': cpu_std,
                                'z_score': (current_cpu - cpu_mean) / cpu_std
                            })
                        
                        if memory_std > 0 and (current_memory - memory_mean) / memory_std > 3:
                            anomalies.append({
                                'type': 'node_memory_spike',
                                'node': node_name,
                                'value': current_memory,
                                'mean': memory_mean,
                                'std': memory_std,
                                'z_score': (current_memory - memory_mean) / memory_std
                            })
            
            # Check for VMs with unusual resource usage
            for vm_id, history in self.vm_performance_history.items():
                # Need enough history for anomaly detection
                if len(history) >= 5:
                    cpu_values = [entry.get('cpu', 0) for entry in history[-5:]]
                    cpu_mean = sum(cpu_values) / 5
                    cpu_std = (sum((x - cpu_mean) ** 2 for x in cpu_values) / 5) ** 0.5
                    
                    current_cpu = history[-1].get('cpu', 0)
                    
                    # Check for significant deviation
                    if cpu_std > 0 and (current_cpu - cpu_mean) / cpu_std > 3:
                        # Get VM details
                        vm_node = history[-1].get('node')
                        vm_status = self.proxmox_api.get_vm_status(vm_node, vm_id) if vm_node else None
                        vm_name = vm_status.get('name', f'VM-{vm_id}') if vm_status else f'VM-{vm_id}'
                        
                        anomalies.append({
                            'type': 'vm_cpu_spike',
                            'vm_id': vm_id,
                            'vm_name': vm_name,
                            'node': vm_node,
                            'value': current_cpu,
                            'mean': cpu_mean,
                            'std': cpu_std,
                            'z_score': (current_cpu - cpu_mean) / cpu_std
                        })
        
        except Exception as e:
            logger.error(f"Error detecting anomalies: {str(e)}")
        
        return anomalies
        
    def get_health_report(self):
        """
        Generate a comprehensive health report for the cluster
        
        Returns:
            dict: Health report
        """
        report = {
            'timestamp': time.time(),
            'nodes': {},
            'vms': {},
            'migrations': {
                'recent': [],
                'success_rate': 0,
                'total_count': len(self.migration_history)
            },
            'anomalies': self.detect_anomalies()
        }
        
        # Get node status
        nodes_usage = self.proxmox_api.get_resource_usage()
        if nodes_usage:
            for node in nodes_usage:
                node_name = node['name']
                
                # Skip offline nodes
                if node['status'] != 'online':
                    report['nodes'][node_name] = {
                        'status': 'offline',
                        'uptime': 0
                    }
                    continue
                
                # Add node data
                report['nodes'][node_name] = {
                    'status': 'online',
                    'cpu_usage': node['cpu']['usage'],
                    'memory_usage': node['memory']['used'] / node['memory']['total'] if node['memory']['total'] > 0 else 0,
                    'disk_usage': node['disk']['used'] / node['disk']['total'] if node['disk']['total'] > 0 else 0,
                    'uptime': node['uptime'],
                    'load': node.get('load', 0),
                    'is_overloaded': node_name in self.detect_overloaded_nodes(),
                    'is_underloaded': node_name in self.detect_underloaded_nodes()
                }
        
        # Get VM status for each node
        for node_name in report['nodes']:
            if report['nodes'][node_name]['status'] != 'online':
                continue
                
            node_vms = self.proxmox_api.get_node_vms(node_name) or []
            
            for vm in node_vms:
                vm_id = vm['vmid']
                
                # Get VM status
                vm_status = self.proxmox_api.get_vm_status(node_name, vm_id)
                if not vm_status:
                    continue
                
                # Add VM data
                report['vms'][vm_id] = {
                    'name': vm_status.get('name', f'VM-{vm_id}'),
                    'status': vm_status.get('status', 'unknown'),
                    'node': node_name,
                    'cpu_usage': vm_status.get('cpu', 0),
                    'memory_usage': vm_status.get('mem', 0) / vm_status.get('maxmem', 1) if vm_status.get('maxmem', 0) > 0 else 0,
                    'uptime': vm_status.get('uptime', 0),
                    'in_group': False,
                    'group_name': None
                }
                
                # Check if VM is in a group
                for group_name, group_vms in self.config.get("vm_groups", {}).items():
                    if str(vm_id) in group_vms or vm_id in group_vms:
                        report['vms'][vm_id]['in_group'] = True
                        report['vms'][vm_id]['group_name'] = group_name
                        break
        
        # Migration statistics
        recent_migrations = self.migration_history[-10:] if len(self.migration_history) > 10 else self.migration_history
        successful_migrations = [m for m in self.migration_history if m.get('result') == 'success']
        failed_migrations = [m for m in self.migration_history if m.get('result') == 'failed']
        
        # Add success rate
        total_completed = len(successful_migrations) + len(failed_migrations)
        report['migrations']['success_rate'] = len(successful_migrations) / total_completed if total_completed > 0 else 0
        
        # Add recent migrations
        report['migrations']['recent'] = recent_migrations
        report['migrations']['successful_count'] = len(successful_migrations)
        report['migrations']['failed_count'] = len(failed_migrations)
        
        return report