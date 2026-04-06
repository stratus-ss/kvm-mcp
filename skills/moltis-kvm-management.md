---
name: kvm-management
description: Token-efficient KVM/libvirt VM management via MCP. Use when the user asks to create, manage, clone, snapshot, or diagnose virtual machines. Covers VM lifecycle, guest agent operations, fleet management, and disk/network operations -- with strict token-cost guidance.
allowed_tools:
  - mcp__kvm-manager__kvm_list_hosts
  - mcp__kvm-manager__kvm_fleet_status
  - mcp__kvm-manager__kvm_list_vms
  - mcp__kvm-manager__kvm_get_vm_status
  - mcp__kvm-manager__kvm_start_vm
  - mcp__kvm-manager__kvm_stop_vm
  - mcp__kvm-manager__kvm_restart_vm
  - mcp__kvm-manager__kvm_create_vm
  - mcp__kvm-manager__kvm_delete_vm
  - mcp__kvm-manager__kvm_clone_vm
  - mcp__kvm-manager__kvm_get_boot_order
  - mcp__kvm-manager__kvm_set_boot_order
  - mcp__kvm-manager__kvm_create_snapshot
  - mcp__kvm-manager__kvm_list_snapshots
  - mcp__kvm-manager__kvm_delete_snapshot
  - mcp__kvm-manager__kvm_restore_snapshot
  - mcp__kvm-manager__kvm_list_disks
  - mcp__kvm-manager__kvm_attach_disk
  - mcp__kvm-manager__kvm_detach_disk
  - mcp__kvm-manager__kvm_resize_disk
  - mcp__kvm-manager__kvm_list_networks
  - mcp__kvm-manager__kvm_attach_network
  - mcp__kvm-manager__kvm_detach_network
  - mcp__kvm-manager__guest_ping
  - mcp__kvm-manager__guest_get_network
  - mcp__kvm-manager__guest_get_ip
  - mcp__kvm-manager__guest_exec
  - mcp__kvm-manager__guest_inject_ssh_key
  - mcp__kvm-manager__guest_set_hostname
  - mcp__kvm-manager__ensure_vm_running
  - mcp__kvm-manager__ensure_vm_stopped
  - mcp__kvm-manager__ensure_vm_exists
  - mcp__kvm-manager__ensure_snapshot_exists
  - mcp__kvm-manager__ensure_disk_attached
  - mcp__kvm-manager__get_tool_metrics
  - mcp__kvm-manager__query_tool_metrics_history
---

# Token-Efficient KVM Management

## The One Rule

**Never call `guest_get_network` when `guest_get_ip` will do.** Cost difference: 10-50x.

## Token Cost Reference

| Tier | Tools | Approx Tokens |
|------|-------|---------------|
| Free | `guest_ping`, `kvm_start_vm`, `kvm_stop_vm`, `kvm_restart_vm`, `kvm_set_boot_order`, `kvm_create_snapshot`, `kvm_delete_snapshot`, `kvm_attach_disk`, `kvm_detach_disk`, `kvm_attach_network`, `kvm_detach_network`, `guest_inject_ssh_key`, `guest_set_hostname`, `get_tool_metrics` | <100 |
| Cheap | `kvm_get_vm_status`, `kvm_get_boot_order`, `guest_get_ip`, `kvm_list_disks`, `kvm_list_snapshots`, `kvm_list_networks`, `kvm_resize_disk` | <500 |
| Medium | `kvm_list_vms` (filtered), `kvm_fleet_status(summary=True)`, `kvm_list_hosts` | <2k |
| Expensive | `kvm_fleet_status` (full), `kvm_list_vms` (unfiltered), `guest_get_network`, `guest_exec` | 2-10k |

## Multi-Host Support

All tools accept optional `host` parameter. Leave empty for default host.

## Decision Tree: Common Tasks

### Check a VM's status (cheap)
```
kvm_get_vm_status(vm_name)
```
Do NOT use `kvm_list_vms` or resources for single-VM lookups.

### Get a VM's IP (cheap)
```
guest_get_ip(vm_name)
```
Do NOT use `guest_get_network` unless you need all interfaces.

### Fleet overview (medium)
```
kvm_fleet_status(summary=True)
```
Use `summary=True` for counts. Only omit when per-VM details are needed.

### Find specific VMs (medium with filters)
```
kvm_list_vms(name_filter="web", status_filter="running")
```
**Always use filters** when looking for specific VMs.

### Run a command in a VM (bounded)
```
guest_exec(vm_name, "df -h", max_lines=50)
```
Set `max_lines` appropriately. Default is 100. Use 0 only when full output is truly needed.

### Provision from ISO
1. `kvm_create_vm(name, iso_path=..., os_variant=...)`
2. `kvm_set_boot_order(name, "cdrom,hd")`
3. `kvm_start_vm(name)`
4. After install: `kvm_set_boot_order(name, "hd,cdrom")` then `kvm_restart_vm(name)`

### Clone and configure
1. `ensure_vm_stopped(source_vm)`
2. `kvm_clone_vm(source_vm, new_name)`
3. `kvm_start_vm(new_name)`
4. Poll `guest_ping(new_name)` until responsive
5. `guest_inject_ssh_key(new_name, public_key)`
6. `guest_get_ip(new_name)`

### Safe snapshot workflow
1. `ensure_snapshot_exists(vm_name, "before-change")`
2. Make changes...
3. If rollback: `ensure_vm_stopped(vm_name)` -> `kvm_restore_snapshot(vm_name, "before-change", confirm=True)` -> `kvm_start_vm(vm_name)`

## Anti-Patterns -- Do NOT Do These

1. **`kvm_fleet_status` without `summary=True`** when you only need counts.
2. **`guest_get_network`** when you only need an IP address.
3. **`kvm_list_vms` without filters** when searching for a specific VM.
4. **`guest_exec` with unbounded output** -- always set `max_lines` for commands like `ps aux` or `journalctl`.
5. **Using `kvm://vms/{vm_name}` resource** for quick status checks -- use `kvm_get_vm_status` tool.

## Constraints

- VM must be stopped before delete or snapshot restore
- VM/snapshot names: alphanumeric, dots, underscores, hyphens (max 64 chars)
- `guest_exec` allowlist only; shell metacharacters and `..` blocked
- Boot order values: hd,cdrom / cdrom,hd / network / fd
- Disk paths must be under allowed directories

## Self-Monitoring

Call `get_tool_metrics(limit=5)` periodically to check token usage. If expensive tools dominate, switch to cheaper alternatives.

## Troubleshooting

- Guest agent unresponsive -> ensure `qemu-guest-agent` is installed and running in the VM
- Downloads failing -> check disk space with `kvm://storage-pools` resource
- Clone timeout -> ensure source VM is stopped first
- Network issues -> use `guest_exec(vm_name, "ip -br addr")` then `guest_exec(vm_name, "ip route")`
