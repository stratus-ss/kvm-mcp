Use this skill when the user wants to manage KVM/libvirt virtual machines via the kvm-manager MCP server. Trigger if the conversation involves:
- virtual machine, VM, VMs
- KVM, libvirt, QEMU
- snapshot, clone, provision
- guest agent, guest_exec
- "start VM", "stop VM", "create VM", "delete VM"
- fleet status, host management
Do NOT trigger for unrelated topics.

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

All tools accept optional `host` parameter. Leave empty for default host. Use `kvm_list_hosts()` to discover hosts.

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
Use `summary=True` for counts only. Omit only when per-VM details are needed.

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

## Pipeline Step Reference

| # | Tool | Key Params | Notes |
|---|------|-----------|-------|
| 1 | `kvm_create_vm` | `name`, `iso_path`, `os_variant` | Create VM. |
| 2 | `kvm_set_boot_order` | `vm_name`, `boot_order` | cdrom,hd / hd,cdrom / network / fd. |
| 3 | `kvm_start_vm` | `vm_name` | Start stopped VM. |
| 4 | `guest_ping` | `vm_name` | Check agent responsiveness. |
| 5 | `guest_get_ip` | `vm_name` | Primary IP via guest agent. Cheap. |
| 6 | `guest_exec` | `vm_name`, `command`, `max_lines` | Run command. Set max_lines. |

## Constraints

- VM must be stopped before delete or snapshot restore
- VM/snapshot names: alphanumeric, dots, underscores, hyphens (max 64 chars)
- `guest_exec` allowlist only; shell metacharacters and `..` blocked
- Boot order values: hd,cdrom / cdrom,hd / network / fd
- Disk paths must be under allowed directories

## Self-Monitoring

Call `get_tool_metrics(limit=5)` periodically to check token usage. If expensive tools dominate, switch to cheaper alternatives.

## Activation

When activated, confirm the target host (or use default), then proceed with the decision tree. Prefer `ensure_*` idempotent tools when available.
