# KVM MCP Server

Model Context Protocol server for managing KVM/libvirt virtual machines across single or multiple hosts. Exposes comprehensive VM lifecycle, snapshots, disks, networks, fleet management, and guest agent operations as MCP tools consumable by LLM agents (Cursor, Claude Desktop, etc.).

## Quick start

```bash
# Setup
bash scripts/setup.sh

# Configure
cp env.example .env
# Edit .env with your KVM host settings (single host or multi-host YAML)

# Run via Cursor
# Add .cursor/mcp.json to your workspace, then restart Cursor

# Run standalone (stdio transport)
source kvm-venv/bin/activate  # or .venv/bin/activate
python -m app.mcp_server

# Run as HTTP service
export MCP_TRANSPORT=http
export MCP_HOST=0.0.0.0
export MCP_PORT=8000
python -m app.mcp_server

# Run with streamable HTTP transport
export MCP_TRANSPORT=streamable-http
python -m app.mcp_server
```

## What it provides

- **34 tools**: Fleet management, VM lifecycle (create/start/stop/restart/delete/clone), snapshots, disks, networks, boot order, guest agent operations, plus outcome-focused idempotent tools
- **8 resources**: Host status, VM lists, network lists, storage pools, VM details with snapshots and disk layouts  
- **8 prompts**: Guided workflows for provisioning, cloning, snapshots, investigation, deletion, fleet audit, disk resizing, network troubleshooting
- **Multi-host support**: Manage VMs across multiple KVM hosts with centralized fleet overview
- **Audit logging**: Structured JSONL audit trail for all tool calls and resource reads

## Key Features

### VM Management
- **Lifecycle**: Create, start, stop, restart, delete, clone VMs with safety checks
- **Provisioning**: Boot from ISO with guided OS installation workflows
- **Templates**: Clone existing VMs with optional resource modifications
- **Boot Control**: Manage boot device order (hd, cdrom, network, fd)
- **Outcome-Focused Tools**: Idempotent operations like `ensure_vm_running`, `ensure_vm_exists`, `ensure_snapshot_exists` that achieve desired state regardless of current state

### Data Protection  
- **Snapshots**: Create, list, delete, and restore VM snapshots with safety workflows
- **Safety-first**: Automatic snapshot creation before risky operations
- **Rollback**: Easy restore capabilities for failed operations

### Storage Management
- **Disk Operations**: List, attach, detach, and resize VM disks
- **Storage Monitoring**: Track storage pool capacity and utilization
- **Path Security**: Configurable allowed paths for disks and ISO files

### Network Operations
- **Network Management**: List, attach, and detach libvirt networks

### Security & Access Control
- **RBAC System**: Role-based access control with predefined roles (admin, operator, developer, viewer, guest)
- **Path Validation**: Runtime enforcement of allowed disk and ISO paths with path traversal prevention
- **Confirmation Prompts**: Required confirmation for destructive operations (delete VM, restore snapshot, force stop)
- **Rate Limiting**: Per-user operation rate limits and concurrent operation controls
- **Host Restrictions**: Limit user access to specific KVM hosts
- **VM Pattern Matching**: Restrict user access to VMs matching specific name patterns

### Transport Options
- **Multiple Transports**: Support for stdio, HTTP, and streamable HTTP protocols
- **HTTP API**: RESTful web service mode for network access and integration
- **Authentication**: Optional token-based authentication for HTTP transport
- **CORS Support**: Configurable cross-origin resource sharing for web integration  
- **IP Discovery**: Automatic IP address detection via guest agent
- **Network Diagnostics**: Built-in network troubleshooting workflows

### Guest Agent Integration
- **Command Execution**: Run allowlisted commands inside VMs safely
- **SSH Key Injection**: Automated SSH public key deployment  
- **Network Information**: Get interface details and IP addresses
- **Health Checks**: Ping guest agent for connectivity verification

### Multi-Host Fleet Management
- **Host Discovery**: Automatic detection of configured KVM hosts
- **Fleet Overview**: Combined status of all VMs across all hosts
- **Host Targeting**: Direct operations to specific hosts via `host` parameter
- **Connection Status**: Monitor host connectivity and availability

### Enterprise Features
- **Configuration**: Single host via environment or multi-host via YAML
- **Security**: Path restrictions, command allowlisting, SSH key-based auth
- **Audit Trail**: Comprehensive logging of all operations for compliance
- **Containerization**: Rootless container support with sudo disable option

## Configuration

### Single Host Setup (Environment Variables)
For managing a single KVM host or local libvirt:

```bash
cp env.example .env
# Edit .env:
KVM_HOST=your-kvm-host.local      # Leave empty for local libvirt
KVM_HOST_USER=root
KVM_HOST_SSH_KEY=~/.ssh/id_rsa
ALLOWED_DISK_PATHS=/var/lib/libvirt/images
ALLOWED_ISO_PATHS=/var/lib/libvirt/images,/home
```

### Multi-Host Setup (YAML Configuration) 
For enterprise environments with multiple KVM hosts:

```yaml
# hosts.yaml
default_host: production
hosts:
  - name: production
    uri: qemu+ssh://root@prod-kvm.local/system
    ssh_user: root
    ssh_key: ~/.ssh/prod_rsa
    allowed_disk_paths: /var/lib/libvirt/images,/storage/vms
  - name: development
    uri: qemu+ssh://admin@dev-kvm.local/system  
    ssh_user: admin
    ssh_key: ~/.ssh/dev_rsa
    allowed_disk_paths: /var/lib/libvirt/images
```

Then set `KVM_HOSTS_FILE=hosts.yaml` in your `.env` file.

### Security Options
- `ALLOWED_DISK_PATHS`: Comma-separated paths where VM disks can be stored
- `ALLOWED_ISO_PATHS`: Comma-separated paths where ISO files can be accessed
- `KVM_DISABLE_SUDO=true`: For containerized/rootless deployments
- `MCP_AUDIT_LOG_DIR`: Directory for structured audit logs

## Testing

```bash
# Unit tests (mocked, no KVM host needed)
./kvm-venv/bin/python -m pytest tests/test_mcp/ -v

# End-to-end tests (requires KVM host with sudo)
bash scripts/test_mcp.sh
```

## Usage Examples

These tools are invoked by MCP-compatible clients (Claude, Cursor, etc.). Tool names and parameters:

### Basic VM Operations
```
# Discover infrastructure
kvm_list_hosts
kvm_fleet_status

# Create and provision a new VM  
kvm_create_vm(name="ubuntu-test", memory_mb=4096, vcpus=4, disk_size_gb=50)
kvm_set_boot_order(vm_name="ubuntu-test", boot_order="cdrom,hd")
kvm_start_vm(vm_name="ubuntu-test")

# Clone an existing template
kvm_clone_vm(source_vm_name="ubuntu-template", target_name="new-vm")
kvm_start_vm(vm_name="new-vm") 
guest_ping(vm_name="new-vm")  # Wait for guest agent
guest_inject_ssh_key(vm_name="new-vm", public_key="ssh-ed25519 AAAA...")
guest_get_ip(vm_name="new-vm")
```

### Multi-Host Operations
```
# Target specific hosts
kvm_list_vms(host="production")
kvm_create_vm(name="test-vm", host="development") 
kvm_start_vm(vm_name="production-vm", host="production")

# Fleet-wide operations  
kvm_fleet_status  # All VMs across all hosts
```

### Safety and Snapshots
```
# Safe operations with snapshots
kvm_create_snapshot(vm_name="critical-vm", snapshot_name="before-update")
# ... perform risky changes ...
# If something goes wrong:
kvm_stop_vm(vm_name="critical-vm")
kvm_restore_snapshot(vm_name="critical-vm", snapshot_name="before-update")
kvm_start_vm(vm_name="critical-vm")
```

## Remote host hardening

```bash
# On the KVM host (as root)
bash scripts/setup_remote_host.sh

# On this machine (MCP client)
bash scripts/setup_ssh_keys.sh <kvm-host>
```

## Docker Deployment

Run the KVM MCP server in containers with full libvirt access:

```bash
# Build and run with Docker Compose
docker-compose up --build

# Or build manually
docker build -t kvm-mcp-server .

# Run with local libvirt access (privileged mode required)
docker run -d \
  --name kvm-mcp-server \
  --privileged \
  -v /var/run/libvirt:/var/run/libvirt \
  -v ~/.ssh:/root/.ssh:ro \
  -v ./config:/app/config:ro \
  -v ./.env:/app/.env:ro \
  kvm-mcp-server

# For multi-host deployments, mount your hosts.yaml config:
# -v ./config/hosts.yaml:/app/config/hosts.yaml:ro
```

## RBAC & Security Configuration

Enhanced role-based access control with operation filtering and rate limiting:

```bash
# Enable RBAC in .env
SECURITY_AUTH_REQUIRED=true
SECURITY_MAX_CONCURRENT_OPS=10
SECURITY_RATE_LIMIT_PER_MINUTE=100

# Allow only specific operations (comma-separated patterns)
SECURITY_ALLOWED_OPERATIONS=kvm_list_*,guest_ping,guest_get_*

# Or allow all operations in development
SECURITY_ALLOWED_OPERATIONS=*
```

**Operation Patterns:**
- `*` - Allow all operations
- `kvm_*` - All KVM operations  
- `guest_*` - All guest agent operations
- `kvm_list_*,kvm_get_*` - Read-only VM operations
- `kvm_create_*,kvm_delete_*` - VM lifecycle operations

## Attribution & Acknowledgments

This project incorporates security and deployment concepts from the following open source projects:

### libvirt-mcp-server (MIT License)
**Original Project:** https://github.com/coolnyx/libvirt-mcp-server  
**License:** MIT License  
**Attribution:** Security configuration structure, RBAC operation filtering concepts, and Docker deployment patterns adapted from coolnyx/libvirt-mcp-server

**Features Adapted:**
- RBAC configuration with `allowed_operations` patterns
- Security middleware architecture  
- Docker containerization approach with privileged libvirt access
- Enhanced path validation security layers

All adapted code maintains original copyright notices where applicable and is relicensed under AGPL v3 as permitted by the MIT License.

## License

This project is licensed under the **GNU Affero General Public License v3.0** (AGPL v3).

See the [LICENSE](LICENSE) file for the full license text.

**Key points:**
- ✅ Free to use, modify, and distribute
- ✅ Can incorporate MIT and other permissive licensed code  
- ⚠️ **Network copyleft:** If you run a modified version as a network service, you must make the source code available to users
- ⚠️ All modifications and derivative works must be AGPL v3 licensed

**Why AGPL v3?** This ensures that improvements to KVM management infrastructure remain open source and available to the community, even when deployed as cloud services.

## Related

See `../kvm-api/` for the FastAPI REST API that exposes the same KVM operations over HTTP.
