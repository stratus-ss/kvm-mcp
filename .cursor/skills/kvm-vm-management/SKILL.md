---
name: kvm-vm-management
description: Token-efficient KVM/libvirt VM management via MCP. Guides cheap tool choices (guest_get_ip over guest_get_network), filtering, summary modes, and the full VM lifecycle. Use when working with VMs, KVM, libvirt, snapshots, or guest agent operations.
alwaysApply: false
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

### Check a VM's status
```
kvm_get_vm_status(vm_name)
```
Done. Do NOT use `kvm_list_vms` or `kvm://vms/{vm_name}` resource for single-VM lookups.

### Get a VM's IP
```
guest_get_ip(vm_name)
```
Done. Do NOT use `guest_get_network` unless you need all interfaces.

### Fleet overview
```
kvm_fleet_status(summary=True)
```
Use `summary=True` for counts. Only omit `summary` when you need per-VM details.

### Find specific VMs
```
kvm_list_vms(name_filter="web", status_filter="running")
```
Always use filters when looking for specific VMs.

### Run a command in a VM
```
guest_exec(vm_name, "df -h", max_lines=50)
```
Set `max_lines` appropriately. Default is 100. Use 0 only when you need full output.

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
3. If rollback needed: `ensure_vm_stopped(vm_name)` -> `kvm_restore_snapshot(vm_name, "before-change", confirm=True)` -> `kvm_start_vm(vm_name)`

## Anti-Patterns

1. **`kvm_fleet_status` without `summary=True`** when you only need counts -- wastes tokens on full VM lists.
2. **`guest_get_network`** when you only need IP -- use `guest_get_ip` instead.
3. **`kvm_list_vms` without filters** when searching for a specific VM -- use `name_filter` or `status_filter`.
4. **`guest_exec` with commands that produce huge output** (e.g. `journalctl`) without setting `max_lines` -- set a reasonable limit.
5. **Using `kvm://vms/{vm_name}` resource** for quick status checks -- it fetches extra info. Use `kvm_get_vm_status` tool instead.

## Constraints

- VM must be stopped before delete or snapshot restore
- VM/snapshot names: alphanumeric, dots, underscores, hyphens (max 64 chars)
- `guest_exec` allowlist: ls, pwd, whoami, date, uname, hostname, df, free, ps, ip, uptime, head, tail, wc, sort, uniq, which, type, cat, systemctl, journalctl, grep, ss, lsblk, lscpu, mount, id, stat, findmnt
- Shell metacharacters and path traversal blocked

## Self-Monitoring

Call `get_tool_metrics(limit=5)` to check recent token usage. If expensive tools are dominating, switch to cheaper alternatives.
