#!/usr/bin/env python3
"""
Node Selector Module
Uses AI-based decision making to select optimal nodes for AI infrastructure services.
"""

import logging
import numpy as np
from collections import defaultdict

logger = logging.getLogger('san-o1-deployer.node_selector')

class NodeSelector:
    """Intelligent node selector for optimal resource allocation."""
    
    def __init__(self, proxmox, config):
        """Initialize the node selector with Proxmox API and configuration."""
        self.proxmox = proxmox
        self.config = config
        self.node_weights = config.get('node_weights', {
            'cpu': 0.3,
            'memory': 0.3,
            'disk': 0.2,
            'network': 0.1,
            'gpu': 0.1
        })
        # Storage preference order
        self.storage_preferences = config.get('storage_preferences', [])
        # Minimum free resources required per node (percentage)
        self.min_free = config.get('min_free', {
            'cpu': 10,
            'memory': 15,
            'disk': 20
        })
        # Service resource requirements
        self.service_requirements = config.get('service_requirements', {})
        # GPU requirements for services
        self.gpu_requirements = config.get('gpu_requirements', {})
        # Affinity rules (services that should run on same node)
        self.affinity = config.get('affinity', [])
        # Anti-affinity rules (services that should not run on same node)
        self.anti_affinity = config.get('anti_affinity', [])
    
    def get_node_info(self):
        """Gather comprehensive information about all nodes."""
        nodes = self.proxmox.get_nodes()
        node_info = {}
        
        for node in nodes:
            node_name = node['node']
            try:
                # Get basic node statistics
                status = self.proxmox.get_node_status(node_name)
                
                # Try to get resources, but handle if endpoint not available
                try:
                    resources = self.proxmox.get_node_resources(node_name)
                except Exception as e:
                    # If resources endpoint returns 501, it's not implemented in this Proxmox version
                    if "501" in str(e):
                        logger.warning(f"Resources endpoint not implemented for node {node_name}, using basic information only")
                        resources = []  # Set empty resources, we'll work with node status only
                    else:
                        raise  # Re-raise other exceptions
                
                # Get storage information
                storage_list = self.proxmox.get_storage(node_name)
                storages = {s['storage']: s for s in storage_list}
                
                # Get GPU information
                gpu_devices = self.proxmox.get_node_gpu_info(node_name)
                
                # Calculate current utilization
                cpu_used_pct = status['cpu'] * 100
                mem_total = status['memory']['total']
                mem_used = status['memory']['used'] 
                mem_used_pct = (mem_used / mem_total) * 100 if mem_total else 0
                
                # Calculate available resources
                cpu_free_pct = 100 - cpu_used_pct
                mem_free = mem_total - mem_used
                
                # Storage calculation
                storage_free = {}
                for storage_name, preference in self.storage_preferences:
                    if storage_name in storages:
                        s = storages[storage_name]
                        total = s.get('total', 0)
                        used = s.get('used', 0)
                        free = total - used
                        free_pct = (free / total) * 100 if total else 0
                        storage_free[storage_name] = {
                            'free': free,
                            'free_pct': free_pct,
                            'total': total,
                            'preference': preference
                        }
                
                # Determine GPU capabilities
                gpu_info = {
                    'has_nvidia': any('nvidia' in dev.get('device_name', '').lower() for dev in gpu_devices),
                    'nvidia_count': sum(1 for dev in gpu_devices if 'nvidia' in dev.get('device_name', '').lower()),
                    'devices': gpu_devices
                }
                
                # Store comprehensive node information
                node_info[node_name] = {
                    'status': status,
                    'cpu': {
                        'total': status['cpuinfo']['cpus'],
                        'used_pct': cpu_used_pct,
                        'free_pct': cpu_free_pct
                    },
                    'memory': {
                        'total': mem_total,
                        'used': mem_used,
                        'free': mem_free,
                        'used_pct': mem_used_pct,
                        'free_pct': 100 - mem_used_pct
                    },
                    'storage': storage_free,
                    'gpu': gpu_info,
                    'vms': self.proxmox.get_qemu_vms(node_name),
                    'containers': self.proxmox.get_lxc_containers(node_name),
                    'resources': resources
                }
                
                logger.debug(f"Collected information for node {node_name}")
            except Exception as e:
                logger.error(f"Failed to collect information for node {node_name}: {str(e)}")
        
        return node_info
    
    def calculate_node_scores(self, node_info, service_requirements):
        """Calculate fitness scores for each node based on service requirements."""
        node_scores = {}
        
        for node_name, info in node_info.items():
            # Skip nodes that don't meet minimum free resource requirements
            if info['cpu']['free_pct'] < self.min_free['cpu'] or \
               info['memory']['free_pct'] < self.min_free['memory']:
                logger.debug(f"Node {node_name} doesn't meet minimum free resource requirements")
                continue
            
            # Instead of skipping nodes without GPUs entirely, we'll let them be considered
            # for non-GPU services. The allocate_services method will handle matching
            # GPU-requiring services only to nodes with GPUs.
            has_gpu = info['gpu']['has_nvidia']
            if not has_gpu:
                logger.debug(f"Node {node_name} doesn't have GPU capability. Will only consider for non-GPU services.")
            
            # Calculate weighted score for this node
            weights = self.node_weights
            score = (
                weights['cpu'] * info['cpu']['free_pct'] +
                weights['memory'] * info['memory']['free_pct']
            )
            
            # Add storage score - weighted average of free space across preferred storages
            if info['storage']:
                storage_scores = [s['free_pct'] * s['preference'] for s in info['storage'].values()]
                storage_weights = [s['preference'] for s in info['storage'].values()]
                avg_storage_score = sum(storage_scores) / sum(storage_weights) if sum(storage_weights) > 0 else 0
                score += weights['disk'] * avg_storage_score
            
            # Add GPU score if node has GPUs
            if info['gpu']['has_nvidia']:
                score += weights['gpu'] * 100  # Max score for having NVIDIA GPU
            
            node_scores[node_name] = score
            logger.debug(f"Node {node_name} score: {score:.2f}")
        
        return node_scores
    
    def allocate_services(self, node_info, node_scores, service_requirements):
        """Allocate services to nodes based on scores and requirements."""
        allocations = {}
        assigned_nodes = defaultdict(list)
        
        # Sort services by resource intensity (most demanding first)
        sorted_services = sorted(
            service_requirements.items(),
            key=lambda x: (
                x[1].get('memory', 0) + 
                x[1].get('cpu', 0) * 1000 + 
                (10000 if x[1].get('gpu') == 'nvidia' else 0)
            ),
            reverse=True
        )
        
        for service_name, reqs in sorted_services:
            best_node = None
            best_score = -1
            
            # Check if service has affinity with already allocated services
            affinity_nodes = set()
            for affinity_group in self.affinity:
                if service_name in affinity_group:
                    for affinity_service in affinity_group:
                        if affinity_service in allocations:
                            affinity_nodes.add(allocations[affinity_service])
            
            # Check if service has anti-affinity with already allocated services
            anti_affinity_nodes = set()
            for anti_affinity_group in self.anti_affinity:
                if service_name in anti_affinity_group:
                    for anti_affinity_service in anti_affinity_group:
                        if anti_affinity_service in allocations:
                            anti_affinity_nodes.add(allocations[anti_affinity_service])
            
            # Find best node for this service
            for node_name, score in sorted(node_scores.items(), key=lambda x: x[1], reverse=True):
                node = node_info[node_name]
                
                # Skip if this service has anti-affinity with services on this node
                if node_name in anti_affinity_nodes:
                    continue
                
                # Check if node meets specific requirements
                if reqs.get('gpu') == 'nvidia' and not node['gpu']['has_nvidia']:
                    continue
                
                # Check if node has enough resources
                if reqs.get('memory', 0) > node['memory']['free'] * 0.9:  # Leave 10% buffer
                    continue
                
                # If service has affinity requirements, prefer those nodes
                if affinity_nodes and node_name not in affinity_nodes:
                    # Only consider affinity nodes if they're viable
                    if any(n for n in affinity_nodes if n in node_scores):
                        continue
                
                # This node is viable
                if score > best_score:
                    best_node = node_name
                    best_score = score
            
            if best_node:
                allocations[service_name] = best_node
                assigned_nodes[best_node].append(service_name)
                
                # Update node resources (simple approximation)
                if 'memory' in reqs:
                    node_info[best_node]['memory']['free'] -= reqs['memory']
                
                logger.info(f"Assigned service {service_name} to node {best_node} (score: {best_score:.2f})")
            else:
                logger.warning(f"Could not find suitable node for service {service_name}")
        
        return allocations
    
    def analyze_and_allocate(self):
        """Main method to analyze nodes and allocate services."""
        logger.info("Starting node analysis and service allocation")
        
        # Get node information
        node_info = self.get_node_info()
        logger.info(f"Collected information for {len(node_info)} nodes")
        
        # Calculate node scores
        node_scores = self.calculate_node_scores(node_info, self.service_requirements)
        logger.info(f"Calculated scores for {len(node_scores)} eligible nodes")
        
        # Allocate services to nodes
        allocations = self.allocate_services(node_info, node_scores, self.service_requirements)
        
        return allocations
