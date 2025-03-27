#!/usr/bin/env python3
"""
San-O1 Proxmox AI Infrastructure Remote Deployment Script
This script allows for remote execution of the deployment process.
"""

import os
import sys
import argparse
import subprocess
import paramiko
import yaml

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Remote deploy AI infrastructure to Proxmox")
    parser.add_argument('--host', type=str, required=True, help="Target host for deployment")
    parser.add_argument('--user', type=str, required=True, help="SSH username")
    parser.add_argument('--key', type=str, help="SSH private key file")
    parser.add_argument('--password', type=str, help="SSH password (if not using key)")
    parser.add_argument('--config', type=str, default='config.yaml', help="Path to config file")
    return parser.parse_args()

def read_config(config_path):
    """Read the YAML configuration file."""
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

def setup_remote_connection(host, username, key_file=None, password=None):
    """Set up SSH connection to the remote host."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        if key_file:
            key = paramiko.RSAKey.from_private_key_file(key_file)
            client.connect(hostname=host, username=username, pkey=key)
        else:
            client.connect(hostname=host, username=username, password=password)
        return client
    except Exception as e:
        print(f"Error connecting to {host}: {str(e)}")
        sys.exit(1)

def upload_files(sftp, local_dir='.', remote_dir='/tmp/san-o1-deployer'):
    """Upload project files to the remote server."""
    try:
        # Create remote directory if it doesn't exist
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            # Directory probably already exists
            pass
        
        # Upload all project files except .git directory
        for root, dirs, files in os.walk(local_dir):
            if '.git' in root:
                continue
                
            # Create equivalent directory structure on remote
            if root != '.':
                remote_path = os.path.join(remote_dir, root)
                try:
                    sftp.mkdir(remote_path)
                except IOError:
                    # Directory probably already exists
                    pass
            
            # Upload files
            for file in files:
                if file.endswith('.py') or file == 'config.yaml' or file == 'requirements.txt':
                    local_path = os.path.join(root, file)
                    if root == '.':
                        remote_path = os.path.join(remote_dir, file)
                    else:
                        remote_path = os.path.join(remote_dir, root, file)
                    sftp.put(local_path, remote_path)
                    print(f"Uploaded {local_path} to {remote_path}")
        
        return True
    except Exception as e:
        print(f"Error uploading files: {str(e)}")
        return False

def install_dependencies(ssh):
    """Install required dependencies on the remote server."""
    commands = [
        "sudo apt-get update",
        "sudo apt-get install -y python3 python3-pip",
        f"cd /tmp/san-o1-deployer && pip3 install -r requirements.txt"
    ]
    
    for cmd in commands:
        print(f"Executing: {cmd}")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        
        if exit_status != 0:
            print(f"Error executing {cmd}: {stderr.read().decode()}")
            return False
        
        print(stdout.read().decode())
    
    return True

def execute_deployment(ssh):
    """Execute the deployment script on the remote server."""
    command = "cd /tmp/san-o1-deployer && python3 main.py"
    print(f"Executing: {command}")
    
    # Use get_pty=True to see realtime output
    stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)
    
    # Print output in real-time
    for line in stdout:
        print(line.strip('\n'))
    
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        print(f"Deployment failed with exit code {exit_status}")
        print(f"Error: {stderr.read().decode()}")
        return False
    
    return True

def main():
    """Main function to execute remote deployment."""
    args = parse_arguments()
    
    # Read configuration
    config = read_config(args.config)
    
    # Connect to remote host
    print(f"Connecting to {args.host} as {args.user}...")
    ssh = setup_remote_connection(args.host, args.user, args.key, args.password)
    
    # Upload files
    print("Uploading deployment files...")
    sftp = ssh.open_sftp()
    if not upload_files(sftp, local_dir='.', remote_dir='/tmp/san-o1-deployer'):
        ssh.close()
        sys.exit(1)
    
    # Install dependencies
    print("Installing dependencies...")
    if not install_dependencies(ssh):
        ssh.close()
        sys.exit(1)
    
    # Execute deployment
    print("Executing deployment...")
    if not execute_deployment(ssh):
        ssh.close()
        sys.exit(1)
    
    print("Deployment completed successfully!")
    ssh.close()

if __name__ == "__main__":
    main()
