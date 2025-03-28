#!/usr/bin/env python3
import requests
import json
import time
from urllib3.exceptions import InsecureRequestWarning

# Suppress only the specific InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

class ProxmoxAPI:
    """Class to interact with the Proxmox API"""
    
    def __init__(self, host, user, password, realm='pam', verify_ssl=False, port=8006):
        """
        Initialize the Proxmox API connection
        
        Args:
            host (str): Proxmox host IP or hostname
            user (str): Username for authentication
            password (str): Password for authentication
            realm (str): Authentication realm (pam, pve, etc.)
            verify_ssl (bool): Whether to verify SSL certificate
            port (int): API port
        """
        self.host = host
        self.user = user
        self.password = password
        self.realm = realm
        self.verify_ssl = verify_ssl
        self.port = port
        self.api_url = f"https://{self.host}:{self.port}/api2/json"
        self.token = None
        self.csrf_token = None
        self.token_expires = 0
        
    def login(self):
        """Authenticate with Proxmox API and get tokens"""
        auth_url = f"{self.api_url}/access/ticket"
        auth_data = {
            "username": f"{self.user}@{self.realm}",
            "password": self.password
        }
        
        try:
            response = requests.post(auth_url, data=auth_data, verify=self.verify_ssl)
            response.raise_for_status()
            
            result = response.json()['data']
            self.token = result['ticket']
            self.csrf_token = result['CSRFPreventionToken']
            # Set token expiration to 2 hours from now
            self.token_expires = time.time() + 7200
            
            return True
        except Exception as e:
            print(f"Authentication failed: {str(e)}")
            return False
    
    def _ensure_authenticated(self):
        """Ensure we have a valid authentication token"""
        if not self.token or time.time() > self.token_expires:
            return self.login()
        return True
    
    def get(self, endpoint, params=None):
        """
        Make a GET request to the Proxmox API
        
        Args:
            endpoint (str): API endpoint (e.g., 'nodes')
            params (dict, optional): Query parameters to include in the request
            
        Returns:
            dict: API response data
        """
        if not self._ensure_authenticated():
            return None
        
        # Split endpoint and parameters if they're in the endpoint string
        if '?' in endpoint:
            endpoint_parts = endpoint.split('?', 1)
            endpoint = endpoint_parts[0]
            
            # Parse query parameters
            query_params = {}
            param_parts = endpoint_parts[1].split('&')
            for part in param_parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    query_params[key] = value
                else:
                    query_params[part] = '1'
            
            # Merge with any provided params
            if params:
                query_params.update(params)
            params = query_params
            
        url = f"{self.api_url}/{endpoint}"
        headers = {"Cookie": f"PVEAuthCookie={self.token}"}
        
        try:
            response = requests.get(url, headers=headers, params=params, verify=self.verify_ssl)
            response.raise_for_status()
            return response.json()['data']
        except Exception as e:
            print(f"GET request failed: {str(e)}")
            return None
    
    def post(self, endpoint, data=None):
        """
        Make a POST request to the Proxmox API
        
        Args:
            endpoint (str): API endpoint
            data (dict): Data to send in the request
            
        Returns:
            dict: API response data
        """
        if not self._ensure_authenticated():
            return None
            
        url = f"{self.api_url}/{endpoint}"
        headers = {
            "Cookie": f"PVEAuthCookie={self.token}",
            "CSRFPreventionToken": self.csrf_token
        }
        
        try:
            response = requests.post(url, data=data, headers=headers, verify=self.verify_ssl)
            response.raise_for_status()
            return response.json()['data']
        except Exception as e:
            print(f"POST request failed: {str(e)}")
            return None
            
    def get_nodes(self):
        """Get list of all nodes in the cluster"""
        return self.get("nodes")
    
    def get_node_status(self, node):
        """Get status information for a specific node"""
        return self.get(f"nodes/{node}/status")
        
    def get_node_vms(self, node):
        """Get all VMs on a specific node"""
        return self.get(f"nodes/{node}/qemu")
        
    def get_node_containers(self, node):
        """Get all LXC containers on a specific node"""
        return self.get(f"nodes/{node}/lxc")
    
    def get_vm_config(self, node, vmid):
        """Get VM configuration"""
        return self.get(f"nodes/{node}/qemu/{vmid}/config")
    
    def get_vm_status(self, node, vmid):
        """Get VM status"""
        return self.get(f"nodes/{node}/qemu/{vmid}/status/current")
    
    def migrate_vm(self, node, vmid, target_node, online=True, with_local_disks=True):
        """
        Migrate a VM to another node
        
        Args:
            node (str): Source node name
            vmid (int): VM ID
            target_node (str): Target node name
            online (bool): Whether to migrate while VM is running
            with_local_disks (bool): Whether to migrate local disks
            
        Returns:
            dict: API response
        """
        data = {
            "target": target_node,
            "online": 1 if online else 0,
            "with-local-disks": 1 if with_local_disks else 0
        }
        
        return self.post(f"nodes/{node}/qemu/{vmid}/migrate", data=data)
    
    def get_cluster_resources(self, resource_type=None):
        """
        Get cluster resources
        
        Args:
            resource_type (str, optional): Filter by resource type (vm, storage, node)
            
        Returns:
            list: Resources in the cluster
        """
        endpoint = "cluster/resources"
        if resource_type:
            endpoint += f"?type={resource_type}"
        
        return self.get(endpoint)
    
    def get_resource_usage(self):
        """Get detailed resource usage information across the cluster"""
        nodes_data = self.get_nodes()
        if not nodes_data:
            return None
            
        result = []
        for node in nodes_data:
            node_name = node['node']
            status = self.get_node_status(node_name)
            
            if status:
                result.append({
                    'name': node_name,
                    'status': node['status'],
                    'cpu': {
                        'cores': status.get('cpuinfo', {}).get('cores', 0),
                        'usage': status.get('cpu', 0)
                    },
                    'memory': {
                        'total': status.get('memory', {}).get('total', 0),
                        'used': status.get('memory', {}).get('used', 0),
                        'free': status.get('memory', {}).get('free', 0)
                    },
                    'disk': {
                        'total': status.get('rootfs', {}).get('total', 0),
                        'used': status.get('rootfs', {}).get('used', 0),
                        'free': status.get('rootfs', {}).get('free', 0)
                    },
                    'uptime': status.get('uptime', 0)
                })
                
        return result
    
    def check_ha_config(self):
        """
        Check if HA (High Availability) is correctly configured
        
        Returns:
            dict: HA configuration status
        """
        return self.get("cluster/ha/status")
    
    def check_cluster_config(self):
        """
        Check if cluster is correctly configured
        
        Returns:
            dict: Cluster configuration status
        """
        return self.get("cluster/config")
        
    def check_ceph_config(self):
        """
        Check if Ceph is configured
        
        Returns:
            dict: Ceph configuration status
        """
        return self.get("cluster/ceph")
    
    def check_storage_replication(self):
        """
        Check if storage replication is configured
        
        Returns:
            list: Storage replication configuration
        """
        return self.get("cluster/replication")
    
    def setup_ha_group(self, group_name, nodes=None):
        """
        Create a HA group if it doesn't exist
        
        Args:
            group_name (str): Name of the HA group
            nodes (list, optional): List of node names to include in the group
            
        Returns:
            dict: API response
        """
        # Check if group already exists
        ha_groups = self.get("cluster/ha/groups")
        if ha_groups and any(group.get('group') == group_name for group in ha_groups):
            return {"status": "exists", "message": f"HA group {group_name} already exists"}
        
        # Create group with provided nodes or all online nodes
        if not nodes:
            all_nodes = self.get_nodes()
            nodes = [node['node'] for node in all_nodes if node['status'] == 'online']
        
        data = {
            "group": group_name,
            "nodes": ",".join(nodes)
        }
        
        return self.post("cluster/ha/groups", data=data)
    
    def setup_ha_resources(self, vm_id, group=None):
        """
        Add a VM to HA resources
        
        Args:
            vm_id (int): VM ID to add to HA
            group (str, optional): HA group name
            
        Returns:
            dict: API response
        """
        # Check if resource already exists
        ha_resources = self.get("cluster/ha/resources")
        if ha_resources and any(res.get('sid') == f"vm:{vm_id}" for res in ha_resources):
            return {"status": "exists", "message": f"VM {vm_id} already in HA resources"}
        
        data = {
            "sid": f"vm:{vm_id}",
            "max_restart": 3,
            "max_relocate": 3,
            "state": "started"
        }
        
        if group:
            data["group"] = group
            
        return self.post("cluster/ha/resources", data=data)
    
    def enable_vm_ha(self, node, vm_id, group=None):
        """
        Enable HA for a specific VM
        
        Args:
            node (str): Node name where VM is located
            vm_id (int): VM ID
            group (str, optional): HA group name
            
        Returns:
            dict: API response
        """
        return self.setup_ha_resources(vm_id, group)
    
    def setup_cluster_options(self, migration_type="secure"):
        """
        Configure cluster-wide options
        
        Args:
            migration_type (str): Migration type (secure, insecure, websocket)
            
        Returns:
            dict: API response
        """
        data = {
            "migration": migration_type
        }
        
        return self.post("cluster/options", data=data)
    
    def setup_storage_replication(self, storage_id, nodes=None):
        """
        Setup storage replication between nodes
        
        Args:
            storage_id (str): Storage ID to replicate
            nodes (list, optional): List of node names for replication
            
        Returns:
            dict: API response
        """
        if not nodes:
            all_nodes = self.get_nodes()
            nodes = [node['node'] for node in all_nodes if node['status'] == 'online']
        
        # This is a placeholder - actual storage replication setup
        # would depend on the storage type and Proxmox version
        return {"status": "not_implemented", "message": "Storage replication setup not implemented"}
    
    def check_proxmox_config_status(self):
        """
        Check overall Proxmox configuration status
        
        Returns:
            dict: Configuration status for different components
        """
        return {
            "cluster": self.check_cluster_config() is not None,
            "ha": self.check_ha_config() is not None,
            "ceph": self.check_ceph_config() is not None,
            "replication": self.check_storage_replication() is not None
        }
    
    def auto_configure_proxmox(self, configure_ha=True, configure_migration=True, ha_group_name="lb-ha-group"):
        """
        Automatically configure Proxmox for better load balancing
        
        Args:
            configure_ha (bool): Whether to configure HA
            configure_migration (bool): Whether to configure migration settings
            ha_group_name (str): Name for the HA group
            
        Returns:
            dict: Configuration results
        """
        results = {
            "ha_configured": False,
            "migration_configured": False,
            "errors": []
        }
        
        # Configure migration settings
        if configure_migration:
            try:
                migration_result = self.setup_cluster_options(migration_type="secure")
                results["migration_configured"] = migration_result is not None
            except Exception as e:
                results["errors"].append(f"Migration configuration failed: {str(e)}")
        
        # Configure HA
        if configure_ha:
            try:
                # Create HA group with all online nodes
                ha_group_result = self.setup_ha_group(ha_group_name)
                results["ha_configured"] = ha_group_result is not None
                
                # Optional: could automatically add important VMs to HA here
                # This would require additional logic to identify important VMs
                
            except Exception as e:
                results["errors"].append(f"HA configuration failed: {str(e)}")
        
        return results