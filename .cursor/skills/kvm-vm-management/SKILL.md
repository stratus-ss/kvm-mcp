---
name: kvm-vm-management
description: Manage KVM/libvirt virtual machines using MCP tools. Covers VM provisioning, cloning, snapshots, deletion, guest agent diagnostics, and multi-host fleet management. Use when working with VMs, KVM, libvirt, virtual machines, snapshots, or guest agent operations.
---

# KVM VM Management Workflows

Use the MCP server tools (prefixed `kvm_` and `guest_`) to execute all operations. Do not use curl or the REST API directly.

## Multi-Host Support

All tools accept an optional `host` parameter to target specific KVM hosts. Leave empty for the default host.

- `kvm_list_hosts()` -- discover all configured hosts
- `kvm_fleet_status()` -- get combined overview of all VMs across all hosts
- Add `host="hostname"` parameter to any tool for multi-host environments

## Core Workflows

### 1. Provision VM from ISO

1. `kvm_create_vm(name, iso_path="/path/to.iso", os_variant="ubuntu24.04")`
2. `kvm_set_boot_order(name, "cdrom,hd")` -- boot from ISO first
3. `kvm_start_vm(name)`
4. After OS install completes, `kvm_set_boot_order(name, "hd,cdrom")`
5. `kvm_restart_vm(name)`
6. Verify with `kvm_get_vm_status(name)`

### 2. Clone and Configure

1. Check source VM status with `kvm_get_vm_status(source_vm)`
2. If running, `kvm_stop_vm(source_vm)`
3. `kvm_clone_vm(source_vm, new_name)`
4. `kvm_start_vm(new_name)`
5. Poll `guest_ping(new_name)` until the guest agent responds
6. `guest_inject_ssh_key(new_name, public_key)`
7. `guest_get_ip(new_name)` -- retrieve IP for SSH access

### 3. Safe Snapshot and Restore

1. `kvm_create_snapshot(vm_name, "before-update")`
2. Perform changes
3. If rollback needed:
   - `kvm_stop_vm(vm_name)`
   - `kvm_restore_snapshot(vm_name, "before-update")`
   - `kvm_start_vm(vm_name)`
4. Once confirmed good, optionally `kvm_delete_snapshot(vm_name, "before-update")`

### 4. Investigate a Running VM

1. `kvm_get_vm_status(vm_name)` -- status and IP
2. `guest_ping(vm_name)` -- check guest agent
3. `guest_exec(vm_name, "uname -a")` -- kernel info
4. `guest_exec(vm_name, "uptime")` -- load averages
5. `guest_exec(vm_name, "free -m")` -- memory usage
6. `guest_exec(vm_name, "df -h")` -- disk usage
7. `guest_exec(vm_name, "ip -br addr")` -- network interfaces

### 5. Clean Deletion

1. `kvm_get_vm_status(vm_name)` -- check state
2. If running, `kvm_stop_vm(vm_name)`. If graceful stop fails, `kvm_stop_vm(vm_name, force=True)`
3. `kvm_delete_vm(vm_name, remove_storage=True)`
4. Confirm with `kvm_list_vms()`

### 6. Disk Management

1. `kvm_list_disks(vm_name)` -- show current disk layout
2. For resizing: `kvm_stop_vm(vm_name)` -> `kvm_create_snapshot(vm_name, "pre-resize")` -> `kvm_resize_disk(vm_name, new_size_gb)` -> `kvm_start_vm(vm_name)`
3. For additional storage: `kvm_attach_disk(vm_name, disk_path)` or `kvm_detach_disk(vm_name, disk_path)`

### 7. Fleet Management

1. `kvm_list_hosts()` -- discover all configured hosts and their status
2. `kvm_fleet_status()` -- get comprehensive view of all VMs across all hosts
3. For each host, check `kvm_list_vms(host=hostname)` for host-specific VM lists
4. Use resources like `kvm://storage-pools` to monitor storage capacity

### 8. Network Troubleshooting

1. `kvm_get_vm_status(vm_name)` -- confirm running
2. `guest_ping(vm_name)` -- check guest agent connectivity
3. `guest_exec(vm_name, "ip -br addr")` -- interface state
4. `guest_exec(vm_name, "ip route")` -- routing table
5. `guest_exec(vm_name, "cat /etc/resolv.conf")` -- DNS configuration
6. `guest_exec(vm_name, "ss -tlnp")` -- listening services
7. `kvm_list_networks()` -- verify libvirt network status

## Constraints

- **Stop before destructive ops**: A VM must be stopped before delete or snapshot restore.
- **Naming**: VM and snapshot names allow alphanumeric, dots, underscores, hyphens only (max 64 chars).
- **Boot order values**: `hd,cdrom`, `cdrom,hd`, `network`, `fd`.
- **Disk paths**: Must be under configured allowed directories (default `/var/lib/libvirt/images/`).
- **guest_exec allowlist**: `ls`, `pwd`, `whoami`, `date`, `uname`, `hostname`, `df`, `free`, `ps`, `ip`, `uptime`, `head`, `tail`, `wc`, `sort`, `uniq`, `which`, `type`, `cat`, `systemctl`, `journalctl`, `grep`, `ss`, `lsblk`, `lscpu`, `mount`, `id`, `stat`, `findmnt`.
- **Blocked in guest_exec**: Shell metacharacters (`| & ; $ \``) and path traversal (`..`).

## Configuration

Configure hosts via environment variables or YAML file:

### Single Host (Environment Variables)
```bash
KVM_HOST=your-host-name
KVM_HOST_USER=root
KVM_HOST_SSH_KEY=~/.ssh/id_rsa
ALLOWED_DISK_PATHS=/var/lib/libvirt/images
ALLOWED_ISO_PATHS=/var/lib/libvirt/images,/home
```

### Multi-Host (YAML File)
```yaml
default_host: primary
hosts:
  - name: primary
    uri: qemu+ssh://root@host1/system
    ssh_user: root
    ssh_key: ~/.ssh/id_rsa
    allowed_disk_paths: /var/lib/libvirt/images
  - name: secondary
    uri: qemu+ssh://root@host2/system
    ssh_user: root
    ssh_key: ~/.ssh/id_rsa
```

## Available MCP Tools

| Category | Tools |
|----------|-------|
| Fleet | `kvm_list_hosts`, `kvm_fleet_status` |
| VM lifecycle | `kvm_list_vms`, `kvm_get_vm_status`, `kvm_start_vm`, `kvm_stop_vm`, `kvm_restart_vm`, `kvm_create_vm`, `kvm_delete_vm`, `kvm_clone_vm` |
| Boot order | `kvm_get_boot_order`, `kvm_set_boot_order` |
| Snapshots | `kvm_create_snapshot`, `kvm_list_snapshots`, `kvm_delete_snapshot`, `kvm_restore_snapshot` |
| Disks | `kvm_list_disks`, `kvm_attach_disk`, `kvm_detach_disk`, `kvm_resize_disk` |
| Networks | `kvm_list_networks`, `kvm_attach_network`, `kvm_detach_network` |
| Guest agent | `guest_ping`, `guest_get_network`, `guest_get_ip`, `guest_exec`, `guest_inject_ssh_key` |

## MCP Resources (read-only)

- `kvm://hosts` -- all configured hosts with connection status
- `kvm://vms` -- all VMs on the default host with statuses
- `kvm://networks` -- all libvirt networks on the default host
- `kvm://storage-pools` -- storage pools with capacity/available space
- `kvm://vms/{vm_name}` -- detailed VM info including IP and extended details
- `kvm://vms/{vm_name}/snapshots` -- snapshot list for a VM
- `kvm://vms/{vm_name}/disks` -- disk layout for a VM (device names, paths, drivers)
- `kvm://hosts/{host_name}/vms` -- all VMs on a specific host

## MCP Prompts (Guided Workflows)

The server includes several built-in prompts for complex workflows:
- `provision_vm_from_iso` -- guided VM creation from ISO
- `clone_and_configure` -- VM cloning with SSH setup
- `snapshot_and_restore` -- safe snapshot workflows
- `investigate_vm` -- comprehensive VM diagnostics
- `delete_vm_safely` -- safe VM deletion
- `fleet_audit` -- audit all hosts and VMs
- `resize_vm_disk` -- safe disk resizing
- `network_troubleshoot` -- network diagnostics

## Best Practices

### Safety First
- Always create snapshots before risky operations
- Stop VMs gracefully before destructive operations
- Use `force=True` only when graceful operations fail
- Monitor storage capacity with `kvm://storage-pools` resource

### Multi-Host Environments
- Use `kvm_list_hosts()` to discover available hosts
- Use `kvm_fleet_status()` for comprehensive fleet overview
- Always specify the `host` parameter when working with specific hosts
- Check host connectivity status before operations

### Guest Agent Dependencies
- Ensure qemu-guest-agent is installed and running in VMs for `guest_*` operations
- Use `guest_ping()` to verify agent connectivity before other guest operations
- SSH key injection requires functioning guest agent

### Performance Considerations
- Use `kvm://storage-pools` to monitor disk space before creating VMs
- Consider VM placement across hosts for load distribution
- Monitor running VM counts per host with fleet status

### Common Environment Adaptations
- **Local development**: Use `qemu:///system` URI for local KVM
- **Remote hosts**: Configure SSH keys and use `qemu+ssh://` URIs
- **Containerized**: Set `KVM_DISABLE_SUDO=true` for rootless containers
- **Enterprise**: Use YAML configuration for multiple hosts with different roles

### Error Handling
- Check return codes and error messages from all operations
- Use `kvm_get_vm_status()` to verify VM state before operations  
- Poll `guest_ping()` with delays when waiting for agent availability
- Have rollback procedures ready (snapshots, force stops)
