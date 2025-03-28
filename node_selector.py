#!/usr/bin/env python3
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from collections import defaultdict

class NodeSelector:
    """Class to select the best node for deploying a VM or container"""
    
    def __init__(self, proxmox_api):
        """
        Initialize the NodeSelector
        
        Args:
            proxmox_api (ProxmoxAPI): Instance of the ProxmoxAPI class
        """
        self.proxmox_api = proxmox_api
        self.resource_history = defaultdict(lambda: {
            'cpu': [],
            'memory': [],
            'disk': [],
            'network': []
        })
        # Default weights - can be adjusted based on importance
        self.weights = {
            'cpu': 0.35,
            'memory': 0.35,
            'disk': 0.2,
            'network': 0.1
        }
    
    def update_resource_history(self):
        """Update resource usage history for all nodes"""
        nodes_usage = self.proxmox_api.get_resource_usage()
        if not nodes_usage:
            return
            
        for node in nodes_usage:
            node_name = node['name']
            if node['status'] == 'online':
                self.resource_history[node_name]['cpu'].append(node['cpu']['usage'])
                mem_used_percent = node['memory']['used'] / node['memory']['total'] if node['memory']['total'] > 0 else 1
                self.resource_history[node_name]['memory'].append(mem_used_percent)
                disk_used_percent = node['disk']['used'] / node['disk']['total'] if node['disk']['total'] > 0 else 1
                self.resource_history[node_name]['disk'].append(disk_used_percent)
                
                # Keep history limited to avoid memory issues
                max_history = 30
                for resource_type in ['cpu', 'memory', 'disk', 'network']:
                    if len(self.resource_history[node_name][resource_type]) > max_history:
                        self.resource_history[node_name][resource_type] = self.resource_history[node_name][resource_type][-max_history:]
    
    def set_weights(self, weights):
        """
        Set new weights for the selection algorithm
        
        Args:
            weights (dict): Dictionary with new weights for cpu, memory, disk, network
        """
        # Validate that weights sum to 1
        total = sum(weights.values())
        if abs(total - 1.0) > 0.01:  # Allow a small margin of error
            print("Warning: Weights should sum to 1.0. Normalizing automatically.")
            weights = {k: v/total for k, v in weights.items()}
            
        self.weights = weights
    
    def predict_future_load(self, node_name, resource_type, hours_ahead=1):
        """
        Predict future load for a specific resource on a node
        
        Args:
            node_name (str): Name of the node
            resource_type (str): Type of resource (cpu, memory, disk, network)
            hours_ahead (int): Number of hours to predict ahead
            
        Returns:
            float: Predicted resource usage
        """
        history = self.resource_history[node_name][resource_type]
        if not history:
            return 0
            
        # Simple linear trend prediction
        if len(history) < 3:
            return history[-1]  # If not enough data, return last value
            
        # Use last few points for prediction
        x = np.array(range(len(history))).reshape(-1, 1)
        y = np.array(history)
        
        # Simple linear regression
        n = len(x)
        x_mean = np.mean(x)
        y_mean = np.mean(y)
        
        # Calculate slope and intercept
        slope = np.sum((x - x_mean) * (y - y_mean)) / np.sum((x - x_mean) ** 2)
        intercept = y_mean - slope * x_mean
        
        # Predict future value
        future_x = n + hours_ahead
        predicted_value = slope * future_x + intercept
        
        # Cap at reasonable values
        predicted_value = max(0, min(1, predicted_value))
        
        return predicted_value
    
    def calculate_node_score(self, node_name, vm_requirements=None):
        """
        Calculate a score for a node based on current load and predictions
        
        Args:
            node_name (str): Name of the node
            vm_requirements (dict, optional): VM requirements for cpu, memory, disk
            
        Returns:
            float: Node score (lower is better)
        """
        if not self.resource_history[node_name]['cpu']:
            self.update_resource_history()
            # If still no data, return infinity (worst score)
            if not self.resource_history[node_name]['cpu']:
                return float('inf')
        
        # Current usage
        current_cpu = self.resource_history[node_name]['cpu'][-1]
        current_memory = self.resource_history[node_name]['memory'][-1]
        current_disk = self.resource_history[node_name]['disk'][-1]
        
        # Predicted usage in 1 hour
        predicted_cpu = self.predict_future_load(node_name, 'cpu')
        predicted_memory = self.predict_future_load(node_name, 'memory')
        predicted_disk = self.predict_future_load(node_name, 'disk')
        
        # Combine current and predicted (giving more weight to current)
        cpu_score = current_cpu * 0.7 + predicted_cpu * 0.3
        memory_score = current_memory * 0.7 + predicted_memory * 0.3
        disk_score = current_disk * 0.7 + predicted_disk * 0.3
        
        # Check if node meets VM requirements
        if vm_requirements:
            # Get node details
            nodes = self.proxmox_api.get_nodes()
            node_info = next((n for n in nodes if n['node'] == node_name), None)
            
            if node_info:
                node_status = self.proxmox_api.get_node_status(node_name)
                
                # Calculate available resources
                available_cpu = node_info.get('maxcpu', 0) - (node_info.get('maxcpu', 0) * current_cpu)
                available_memory = node_status.get('memory', {}).get('free', 0)
                available_disk = node_status.get('rootfs', {}).get('free', 0)
                
                # Check if requirements are met
                if vm_requirements.get('cpu', 0) > available_cpu:
                    return float('inf')  # Not enough CPU
                if vm_requirements.get('memory', 0) > available_memory:
                    return float('inf')  # Not enough memory
                if vm_requirements.get('disk', 0) > available_disk:
                    return float('inf')  # Not enough disk space
        
        # Calculate weighted score
        final_score = (
            cpu_score * self.weights['cpu'] +
            memory_score * self.weights['memory'] +
            disk_score * self.weights['disk']
        )
        
        # Add variability factor based on standard deviation of resource usage
        # This helps avoid nodes with highly variable loads
        if len(self.resource_history[node_name]['cpu']) > 5:
            cpu_std = np.std(self.resource_history[node_name]['cpu'][-5:])
            memory_std = np.std(self.resource_history[node_name]['memory'][-5:])
            variability_factor = (cpu_std + memory_std) / 2
            final_score += variability_factor * 0.1  # Add 10% weight to variability
        
        return final_score
    
    def select_best_node(self, vm_requirements=None, excluded_nodes=None):
        """
        Select the best node for deploying a VM
        
        Args:
            vm_requirements (dict, optional): VM requirements for cpu, memory, disk
            excluded_nodes (list, optional): List of node names to exclude from selection
            
        Returns:
            str: Name of the best node, or None if no suitable node found
        """
        # Update resource history
        self.update_resource_history()
        
        # Get all nodes
        nodes = self.proxmox_api.get_nodes()
        if not nodes:
            return None
            
        excluded_nodes = excluded_nodes or []
        node_scores = {}
        
        for node in nodes:
            node_name = node['node']
            
            # Skip excluded nodes and offline nodes
            if node_name in excluded_nodes or node['status'] != 'online':
                continue
                
            # Calculate score for this node
            score = self.calculate_node_score(node_name, vm_requirements)
            node_scores[node_name] = score
        
        if not node_scores:
            return None
            
        # Return the node with the lowest score
        return min(node_scores.items(), key=lambda x: x[1])[0]
    
    def get_node_recommendations(self, count=3, vm_requirements=None):
        """
        Get multiple node recommendations in order of preference
        
        Args:
            count (int): Number of recommendations to return
            vm_requirements (dict, optional): VM requirements for cpu, memory, disk
            
        Returns:
            list: List of node names in order of preference
        """
        # Update resource history
        self.update_resource_history()
        
        # Get all nodes
        nodes = self.proxmox_api.get_nodes()
        if not nodes:
            return []
            
        node_scores = {}
        
        for node in nodes:
            node_name = node['node']
            
            # Skip offline nodes
            if node['status'] != 'online':
                continue
                
            # Calculate score for this node
            score = self.calculate_node_score(node_name, vm_requirements)
            node_scores[node_name] = score
        
        # Sort nodes by score (lower is better)
        sorted_nodes = sorted(node_scores.items(), key=lambda x: x[1])
        
        # Return the top N nodes
        return [node for node, _ in sorted_nodes[:count]]