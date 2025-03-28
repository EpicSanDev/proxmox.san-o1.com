#!/usr/bin/env python3
from flask import Flask, jsonify, request
import argparse
import logging
import threading
import json
import time
from proxmox_api import ProxmoxAPI
from load_balancer import LoadBalancer

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("load_balancer_api.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ProxmoxLoadBalancerAPI")

app = Flask(__name__)
load_balancer = None

# API authentication (basic implementation)
API_KEYS = {}

def require_api_key(func):
    """Decorator to require API key for routes"""
    def wrapper(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key not in API_KEYS:
            return jsonify({"error": "Invalid or missing API key"}), 401
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

@app.route('/api/status', methods=['GET'])
@require_api_key
def get_status():
    """Get the current status of the load balancer"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    status = load_balancer.get_status()
    return jsonify(status)

@app.route('/api/health', methods=['GET'])
@require_api_key
def get_health():
    """Get a comprehensive health report of the cluster"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    health_report = load_balancer.get_health_report()
    return jsonify(health_report)

@app.route('/api/recommendations', methods=['GET'])
@require_api_key
def get_recommendations():
    """Get migration recommendations"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    detail_level = request.args.get('detail', 'basic')
    
    if detail_level == 'detailed':
        recommendations = load_balancer.get_detailed_recommendations()
    else:
        recommendations = load_balancer.get_recommendations()
        
    return jsonify(recommendations)

@app.route('/api/nodes', methods=['GET'])
@require_api_key
def get_nodes():
    """Get information about all nodes"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    nodes = load_balancer.proxmox_api.get_nodes()
    nodes_usage = load_balancer.proxmox_api.get_resource_usage()
    
    # Combine nodes data with resource usage
    result = []
    for node in nodes:
        node_data = {
            'name': node['node'],
            'status': node['status'],
            'usage': None
        }
        
        # Add usage data if available
        if nodes_usage:
            for usage in nodes_usage:
                if usage['name'] == node['node']:
                    node_data['usage'] = {
                        'cpu': usage['cpu'],
                        'memory': usage['memory'],
                        'disk': usage['disk'],
                        'uptime': usage['uptime']
                    }
                    break
        
        result.append(node_data)
        
    return jsonify({'nodes': result})

@app.route('/api/vms', methods=['GET'])
@require_api_key
def get_vms():
    """Get information about all VMs"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    nodes = load_balancer.proxmox_api.get_nodes()
    
    all_vms = []
    for node in nodes:
        if node['status'] != 'online':
            continue
            
        node_vms = load_balancer.proxmox_api.get_node_vms(node['node']) or []
        
        for vm in node_vms:
            vm_data = {
                'id': vm['vmid'],
                'name': vm.get('name', f"VM-{vm['vmid']}"),
                'status': vm['status'],
                'node': node['node']
            }
            
            # Add VM status details if VM is running
            if vm['status'] == 'running':
                vm_status = load_balancer.proxmox_api.get_vm_status(node['node'], vm['vmid'])
                if vm_status:
                    vm_data['cpu_usage'] = vm_status.get('cpu', 0)
                    vm_data['memory_usage'] = vm_status.get('mem', 0) / vm_status.get('maxmem', 1) if vm_status.get('maxmem', 0) > 0 else 0
            
            all_vms.append(vm_data)
    
    return jsonify({'vms': all_vms})

@app.route('/api/migrate', methods=['POST'])
@require_api_key
def migrate_vm():
    """Manually trigger VM migration"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    data = request.json
    
    # Required parameters
    vm_id = data.get('vm_id')
    source_node = data.get('source_node')
    target_node = data.get('target_node')
    
    if not vm_id or not source_node or not target_node:
        return jsonify({"error": "Missing required parameters (vm_id, source_node, target_node)"}), 400
    
    # Optional parameters
    online = data.get('online', True)
    with_local_disks = data.get('with_local_disks', True)
    
    # Get VM status to verify it exists
    vm_status = load_balancer.proxmox_api.get_vm_status(source_node, vm_id)
    if not vm_status:
        return jsonify({"error": f"VM {vm_id} not found on node {source_node}"}), 404
    
    # Check impact of migration
    impact = load_balancer.analyze_migration_impact(vm_id, source_node, target_node)
    
    # Perform migration
    result = load_balancer.proxmox_api.migrate_vm(source_node, vm_id, target_node, online=online, with_local_disks=with_local_disks)
    
    if result:
        # Record migration
        migration_record = {
            'vm_id': vm_id,
            'source_node': source_node,
            'target_node': target_node,
            'timestamp': time.time(),
            'reason': 'manual',
            'requirements': load_balancer._get_vm_requirements(vm_status),
            'vm_name': vm_status.get('name', f'VM-{vm_id}'),
            'result': 'initiated'
        }
        load_balancer.migration_history.append(migration_record)
        load_balancer.last_balance_time[vm_id] = time.time()
        
        return jsonify({
            "status": "success", 
            "message": f"Migration of VM {vm_id} from {source_node} to {target_node} initiated",
            "impact_analysis": impact
        })
    else:
        return jsonify({"status": "error", "message": f"Failed to migrate VM {vm_id}"}), 500

@app.route('/api/balance', methods=['POST'])
@require_api_key
def balance_cluster():
    """Manually trigger a cluster balance"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    result = load_balancer.balance_cluster()
    
    if result:
        return jsonify({"status": "success", "message": "Cluster balance initiated"})
    else:
        return jsonify({"status": "info", "message": "No migrations were performed"})

@app.route('/api/config', methods=['GET'])
@require_api_key
def get_config():
    """Get the current configuration"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    return jsonify(load_balancer.config)

@app.route('/api/config', methods=['PUT'])
@require_api_key
def update_config():
    """Update the configuration"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    data = request.json
    
    # Validate config changes
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid configuration format"}), 400
    
    # Update configuration (with validation)
    for key, value in data.items():
        if key in load_balancer.config:
            # Special handling for resource weights
            if key == "resource_weights" and isinstance(value, dict):
                # Ensure weights sum to 1
                total = sum(value.values())
                if abs(total - 1.0) > 0.01:
                    # Normalize weights
                    value = {k: v/total for k, v in value.items()}
                load_balancer.config[key] = value
                # Update node selector weights
                load_balancer.node_selector.set_weights(value)
            else:
                load_balancer.config[key] = value
    
    # Save configuration
    load_balancer.save_config()
    
    return jsonify({"status": "success", "message": "Configuration updated", "config": load_balancer.config})

@app.route('/api/vm_groups', methods=['GET'])
@require_api_key
def get_vm_groups():
    """Get VM affinity groups"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    return jsonify({"vm_groups": load_balancer.config.get("vm_groups", {})})

@app.route('/api/vm_groups/update', methods=['POST'])
@require_api_key
def update_vm_groups():
    """Update VM affinity groups automatically"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    success = load_balancer.update_vm_groups()
    
    if success:
        return jsonify({
            "status": "success", 
            "message": "VM groups updated", 
            "vm_groups": load_balancer.config.get("vm_groups", {})
        })
    else:
        return jsonify({"status": "error", "message": "Failed to update VM groups"}), 500

@app.route('/api/critical_vms/update', methods=['POST'])
@require_api_key
def update_critical_vms():
    """Update critical VMs automatically"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    success = load_balancer.update_critical_vms()
    
    if success:
        return jsonify({
            "status": "success", 
            "message": "Critical VMs updated", 
            "critical_vms": load_balancer.config.get("proxmox_config", {}).get("critical_vms", [])
        })
    else:
        return jsonify({"status": "error", "message": "Failed to update critical VMs"}), 500

@app.route('/api/anomalies', methods=['GET'])
@require_api_key
def get_anomalies():
    """Get performance anomalies"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    anomalies = load_balancer.detect_anomalies()
    
    return jsonify({"anomalies": anomalies})

@app.route('/api/migrations/history', methods=['GET'])
@require_api_key
def get_migration_history():
    """Get migration history"""
    if not load_balancer:
        return jsonify({"error": "Load balancer not initialized"}), 500
        
    # Get optional limit parameter
    limit = request.args.get('limit', 10, type=int)
    
    # Get optional filter by VM
    vm_id = request.args.get('vm_id', None, type=int)
    
    history = load_balancer.migration_history
    
    # Apply filters
    if vm_id is not None:
        history = [m for m in history if m['vm_id'] == vm_id]
    
    # Apply limit
    if limit > 0:
        history = history[-limit:]
    
    return jsonify({"migrations": history})

def start_api(host, port, config_file, proxmox_api, api_key):
    global load_balancer, API_KEYS
    
    # Set up API key
    API_KEYS[api_key] = True
    
    # Initialize load balancer
    load_balancer = LoadBalancer(proxmox_api, config_file=config_file)
    
    # Start Flask app
    app.run(host=host, port=port)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proxmox Load Balancer API")
    
    parser.add_argument("--host", default="127.0.0.1", help="API host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="API port (default: 5000)")
    parser.add_argument("--config", default="load_balancer_config.json", help="Load balancer config file")
    parser.add_argument("--api-key", required=True, help="API key for authentication")
    
    # Connection options
    parser.add_argument("--proxmox-host", required=True, help="Proxmox host (IP or hostname)")
    parser.add_argument("--proxmox-user", required=True, help="Proxmox API username")
    parser.add_argument("--proxmox-password", required=True, help="Proxmox API password")
    parser.add_argument("--proxmox-realm", default="pam", help="Proxmox authentication realm (default: pam)")
    parser.add_argument("--proxmox-port", type=int, default=8006, help="Proxmox API port (default: 8006)")
    parser.add_argument("--verify-ssl", action="store_true", help="Verify SSL certificate")
    
    args = parser.parse_args()
    
    # Initialize Proxmox API
    proxmox_api = ProxmoxAPI(
        host=args.proxmox_host,
        user=args.proxmox_user,
        password=args.proxmox_password,
        realm=args.proxmox_realm,
        verify_ssl=args.verify_ssl,
        port=args.proxmox_port
    )
    
    # Test API connection
    if not proxmox_api.login():
        logger.error("Failed to authenticate with Proxmox API")
        import sys
        sys.exit(1)
    
    # Start API
    logger.info(f"Starting Proxmox Load Balancer API on {args.host}:{args.port}")
    start_api(args.host, args.port, args.config, proxmox_api, args.api_key)