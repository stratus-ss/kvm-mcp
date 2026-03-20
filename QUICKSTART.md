# KVM MCP Server - Quick Start Guide

Get up and running with the KVM MCP server in minutes. This guide covers both native installation and Docker deployment options.

## Prerequisites

### System Requirements
- **Host OS**: Linux with KVM/libvirt support
- **Python**: 3.11+ (for native installation)
- **Docker**: 20.10+ (for containerized deployment)
- **KVM Host**: Local or remote libvirt-compatible system

### KVM Host Setup
Your target KVM host needs:
```bash
# Install KVM/libvirt (Ubuntu/Debian)
sudo apt update
sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils

# Enable and start libvirt
sudo systemctl enable libvirtd
sudo systemctl start libvirtd

# Add user to libvirt group (for local access)
sudo usermod -aG libvirt $USER
```

## 🚀 Option 1: Docker Deployment (Recommended)

### Quick Start with Docker Compose

1. **Clone and configure**:
   ```bash
   git clone <your-repo-url>
   cd kvm-mcp
   cp env.example .env
   ```

2. **Edit configuration** (`.env`):
   ```bash
   # For local libvirt
   KVM_HOST=
   
   # For remote host
   KVM_HOST=your-kvm-host.local
   KVM_HOST_USER=root
   KVM_HOST_SSH_KEY=~/.ssh/id_rsa
   
   # Security settings (optional)
   SECURITY_ALLOWED_OPERATIONS=*
   SECURITY_AUTH_REQUIRED=false
   ```

3. **Launch the server**:
   ```bash
   docker-compose up --build
   ```

4. **Verify it's working**:
   ```bash
   # Check container logs
   docker-compose logs kvm-mcp-server
   
   # Test connection (if using MCP client)
   # The server runs on stdio transport by default
   ```

### Manual Docker Run

For more control over the deployment:

```bash
# Build image
docker build -t kvm-mcp-server .

# Run with local libvirt
docker run -d \
  --name kvm-mcp-server \
  --privileged \
  -v /var/run/libvirt:/var/run/libvirt \
  -v ~/.ssh:/root/.ssh:ro \
  -v $(pwd)/.env:/app/.env:ro \
  kvm-mcp-server

# Run with remote hosts (multi-host setup)
docker run -d \
  --name kvm-mcp-server \
  --privileged \
  -v ~/.ssh:/root/.ssh:ro \
  -v $(pwd)/config/hosts.yaml:/app/config/hosts.yaml:ro \
  -v $(pwd)/.env:/app/.env:ro \
  -e KVM_HOSTS_FILE=/app/config/hosts.yaml \
  kvm-mcp-server
```

## 🛠️ Option 2: Native Installation

### 1. Install Dependencies

```bash
# Clone repository
git clone <your-repo-url>
cd kvm-mcp

# Run setup script
bash scripts/setup.sh

# Or manual installation
python -m venv kvm-venv
source kvm-venv/bin/activate
pip install -e .
```

### 2. Configure Environment

```bash
# Copy example configuration
cp env.example .env

# Edit configuration
nano .env
```

**Key configuration options**:
```bash
# Single host setup
KVM_HOST=your-kvm-host.local
KVM_HOST_USER=root
KVM_HOST_SSH_KEY=~/.ssh/id_rsa

# Or leave empty for local libvirt
KVM_HOST=

# Security paths
ALLOWED_DISK_PATHS=/var/lib/libvirt/images
ALLOWED_ISO_PATHS=/var/lib/libvirt/images,/home

# RBAC (optional)
SECURITY_AUTH_REQUIRED=false
SECURITY_ALLOWED_OPERATIONS=*
```

### 3. Start the Server

```bash
# Activate virtual environment
source kvm-venv/bin/activate

# Run MCP server (stdio transport)
python -m app.mcp_server
```

## 🔧 Multi-Host Configuration

For managing multiple KVM hosts, create a `hosts.yaml` file:

```yaml
# hosts.yaml
default_host: production

hosts:
  - name: production
    uri: qemu+ssh://root@prod-kvm.local/system
    ssh_user: root
    ssh_key: ~/.ssh/prod_rsa
    allowed_disk_paths: /var/lib/libvirt/images,/storage/vms
    allowed_iso_paths: /var/lib/libvirt/images,/storage/iso

  - name: development
    uri: qemu+ssh://admin@dev-kvm.local/system
    ssh_user: admin
    ssh_key: ~/.ssh/dev_rsa
    allowed_disk_paths: /var/lib/libvirt/images
    allowed_iso_paths: /var/lib/libvirt/images
```

Then set in your `.env`:
```bash
KVM_HOSTS_FILE=hosts.yaml
```

## 🔒 Production Security Setup

### Enable RBAC

```bash
# In .env file
SECURITY_AUTH_REQUIRED=true
SECURITY_MAX_CONCURRENT_OPS=10
SECURITY_RATE_LIMIT_PER_MINUTE=100

# Allow only safe operations
SECURITY_ALLOWED_OPERATIONS=kvm_list_*,kvm_get_*,guest_ping,guest_get_*

# Or specific tools
SECURITY_ALLOWED_OPERATIONS=kvm_list_hosts,kvm_fleet_status,kvm_list_vms
```

### Common RBAC Patterns

| Use Case | Pattern | Description |
|----------|---------|-------------|
| **Read-only** | `kvm_list_*,kvm_get_*,guest_get_*,guest_ping` | Safe monitoring operations |
| **VM Management** | `kvm_*,guest_*` | Full VM lifecycle control |
| **Development** | `*` | All operations allowed |
| **Snapshot Only** | `kvm_*_snapshot,kvm_list_*` | Snapshot and read operations |

## 📱 First Steps - Create Your First VM

Once the server is running, you can use it with any MCP-compatible client:

### 1. Check Host Status
```
Tool: kvm_list_hosts
```

### 2. View Fleet Overview  
```
Tool: kvm_fleet_status
```

### 3. Create a VM from Template
```
Tool: kvm_clone_vm
- source_vm_name: "ubuntu-template"  
- target_name: "my-new-vm"
- host: "production" (optional)
```

### 4. Start and Access VM
```
Tool: kvm_start_vm
- vm_name: "my-new-vm"

Tool: guest_ping  
- vm_name: "my-new-vm"

Tool: guest_get_ip
- vm_name: "my-new-vm" 
```

### 5. Create VM from ISO
```
Tool: kvm_create_vm
- name: "ubuntu-server"
- memory_mb: 4096
- vcpus: 2
- disk_size_gb: 20
- iso_path: "/var/lib/libvirt/images/ubuntu-22.04.iso"
- os_variant: "ubuntu22.04"
```

## 🔧 Integration with Cursor IDE

Add to your workspace `.cursor/mcp.json`:

```json
{
  "mcp_servers": {
    "kvm-manager": {
      "command": "docker",
      "args": ["exec", "-i", "kvm-mcp-server", "python", "-m", "app.mcp_server"],
      "description": "KVM virtual machine management"
    }
  }
}
```

Or for native installation:
```json
{
  "mcp_servers": {
    "kvm-manager": {
      "command": "/path/to/kvm-mcp/kvm-venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/kvm-mcp",
      "description": "KVM virtual machine management"
    }
  }
}
```

## 🚨 Troubleshooting

### Common Issues

**1. Permission denied accessing libvirt**
```bash
# Add user to libvirt group
sudo usermod -aG libvirt $USER
# Log out and back in, or:
newgrp libvirt
```

**2. Docker container can't access libvirt**
```bash
# Ensure libvirt socket exists
ls -la /var/run/libvirt/
# Restart container with correct socket mounts
docker-compose down && docker-compose up
```

**3. SSH key authentication fails**  
```bash
# Test SSH access manually
ssh -i ~/.ssh/id_rsa root@your-kvm-host.local
# Check key permissions
chmod 600 ~/.ssh/id_rsa
chmod 700 ~/.ssh
```

**4. Path validation errors**
```bash
# Check allowed paths in .env
ALLOWED_DISK_PATHS=/var/lib/libvirt/images,/your/custom/path
# Ensure paths exist on target host
```

**5. RBAC blocking operations**
```bash
# Check current RBAC settings
echo $SECURITY_ALLOWED_OPERATIONS
# Temporarily disable for testing
SECURITY_ALLOWED_OPERATIONS=*
```

### Debug Mode

Enable detailed logging:
```bash
# In .env
LOG_LEVEL=DEBUG

# Check logs
docker-compose logs -f kvm-mcp-server
# or for native
tail -f /var/log/kvm-mcp/mcp-audit.jsonl
```

### Health Checks

**Container health**:
```bash
docker-compose ps
docker exec kvm-mcp-server python -c "from app.config import get_settings; print('OK')"
```

**Host connectivity**:
```bash
# Test libvirt connection
virsh -c qemu+ssh://user@host/system list
```

## 📚 Next Steps

- **Explore Tools**: Use `kvm_list_hosts` and `kvm_fleet_status` to understand your infrastructure
- **Set up Templates**: Create base VMs for quick cloning
- **Configure Monitoring**: Set up audit log collection from `/var/log/kvm-mcp/`
- **Production Hardening**: Enable RBAC and configure allowed operations
- **Backup Strategy**: Use snapshot operations for VM backup workflows

## 🆘 Getting Help

- **Documentation**: See [README.md](README.md) for full feature documentation
- **Issues**: Report bugs via GitHub issues  
- **Logs**: Check `/var/log/kvm-mcp/mcp-audit.jsonl` for detailed operation logs
- **Community**: Contribute improvements and share configurations

---

**License**: GNU Affero General Public License v3.0  
**Attribution**: Incorporates security concepts from coolnyx/libvirt-mcp-server (MIT License)