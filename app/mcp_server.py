"""KVM MCP Server -- Model Context Protocol interface for KVM management."""

import asyncio
import base64
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from app.dependencies import get_services
from app.models.vm import validate_vm_name, validate_snapshot_name
from app.utils.audit import audited_tool, audited_resource
from app.utils.rbac_auth import rbac_protected, viewer_or_higher, operator_or_higher
from app.config import get_settings
from app.middleware.rbac import create_rbac_middleware

SERVER_INSTRUCTIONS = """\
MCP server for managing KVM/libvirt virtual machines across one or more hosts.

## Multi-host support
Every tool accepts an optional `host` parameter.  Leave it empty to target the
default host.  Use `kvm_list_hosts` to discover available hosts and
`kvm_fleet_status` to get a combined overview of all hosts in one call.

## Naming rules
- VM and snapshot names: alphanumeric, dots, underscores, hyphens only (max 64 chars).
- Disk images default to /var/lib/libvirt/images/<vm_name>.qcow2.

## Tool categories
| Category       | Tools |
|----------------|-------|
| Fleet          | kvm_list_hosts, kvm_fleet_status |
| VM lifecycle   | kvm_list_vms, kvm_get_vm_status, kvm_start_vm, kvm_stop_vm, kvm_restart_vm, kvm_create_vm, kvm_delete_vm, kvm_clone_vm |
| Boot order     | kvm_get_boot_order, kvm_set_boot_order |
| Snapshots      | kvm_create_snapshot, kvm_list_snapshots, kvm_delete_snapshot, kvm_restore_snapshot |
| Disks          | kvm_list_disks, kvm_attach_disk, kvm_detach_disk, kvm_resize_disk |
| Networks       | kvm_list_networks, kvm_attach_network, kvm_detach_network |
| Guest agent    | guest_ping, guest_get_network, guest_get_ip, guest_exec, guest_inject_ssh_key, guest_set_hostname |

## Common workflows

### Provision a new VM from ISO
1. kvm_create_vm(name, iso_path="/path/to.iso", os_variant="ubuntu24.04")
2. kvm_set_boot_order(name, "cdrom,hd")
3. kvm_start_vm(name)
4. After OS install completes, kvm_set_boot_order(name, "hd,cdrom")
5. kvm_restart_vm(name)

### Provision a VM from an existing template (clone)
1. kvm_stop_vm(source_vm) if running
2. kvm_clone_vm(source_vm, new_name)
3. kvm_start_vm(new_name)
4. guest_ping(new_name)  -- wait until guest agent responds
5. guest_inject_ssh_key(new_name, public_key)
6. guest_get_ip(new_name)

### Safe snapshot and restore
1. kvm_create_snapshot(vm_name, "before-update")
2. Perform changes...
3. If something goes wrong:
   a. kvm_stop_vm(vm_name)
   b. kvm_restore_snapshot(vm_name, "before-update")
   c. kvm_start_vm(vm_name)

### Delete a VM cleanly
1. kvm_stop_vm(vm_name) or kvm_stop_vm(vm_name, force=True)
2. kvm_delete_vm(vm_name, remove_storage=True)

### Investigate a running VM
1. kvm_get_vm_status(vm_name)
2. guest_ping(vm_name)
3. guest_exec(vm_name, "df -h")
4. guest_exec(vm_name, "free -m")
5. guest_exec(vm_name, "uname -a")

### Resize a VM disk
1. kvm_stop_vm(vm_name) if running
2. kvm_create_snapshot(vm_name, "pre-resize") as safety backup
3. kvm_resize_disk(vm_name, new_size_gb)
4. kvm_start_vm(vm_name)
5. guest_exec(vm_name, "lsblk") to verify new size visible in guest

### Network troubleshooting
1. kvm_get_vm_status(vm_name) -- confirm running
2. guest_ping(vm_name) -- check guest agent
3. guest_exec(vm_name, "ip -br addr") -- interface state
4. guest_exec(vm_name, "ip route") -- routing table
5. guest_exec(vm_name, "cat /etc/resolv.conf") -- DNS config
6. guest_exec(vm_name, "ss -tlnp") -- listening services

### Fleet audit
1. kvm_list_hosts() + kvm_fleet_status() -- overview of all hosts
2. For each running VM: kvm_list_snapshots() -- check backup coverage
3. Read kvm://storage-pools -- check available space

## Constraints
- A VM must be stopped before it can be deleted or restored from a snapshot.
- guest_exec allows: ls, pwd, whoami, date, uname, hostname, df, free, ps, ip, \
uptime, head, tail, wc, sort, uniq, which, type, cat, systemctl, journalctl, \
grep, ss, lsblk, lscpu, mount, id, stat, findmnt.
- Shell metacharacters (| & ; ` $ etc.) and path traversal (..) are blocked.
- Boot order values: 'hd,cdrom', 'cdrom,hd', 'network', 'fd'.
- Disk paths must be under the configured allowed directories.

## Resources (read-only)
- kvm://hosts                      -- all configured hosts with connection status
- kvm://vms                        -- all VMs on the default host
- kvm://networks                   -- all libvirt networks on the default host
- kvm://storage-pools              -- storage pools with capacity/available space
- kvm://vms/{vm_name}              -- detailed VM info
- kvm://vms/{vm_name}/snapshots    -- snapshot list for a VM
- kvm://vms/{vm_name}/disks        -- disk layout for a VM
- kvm://hosts/{host_name}/vms      -- all VMs on a specific host
"""

mcp = FastMCP("KVM Manager", instructions=SERVER_INSTRUCTIONS)

# Initialize RBAC middleware (based on libvirt-mcp-server approach)
_settings = get_settings()
rbac_middleware = create_rbac_middleware(_settings.security) if _settings.security.auth_required or _settings.security.allowed_operations != ["*"] else None


def _services():
    return get_services()


def _format_error(operation: str, stderr: str) -> str:
    return f"Failed to {operation}: {stderr.strip() or 'unknown error'}"


def _apply_rbac_if_enabled(tool_name: str):
    """Apply RBAC decorator if enabled."""
    def decorator(func):
        return rbac_protected(tool_name)(func)
    return decorator


def _requires_confirmation(operation: str, details: str) -> str:
    """Return a confirmation message for destructive operations."""
    return (
        f"⚠️  DESTRUCTIVE OPERATION CONFIRMATION REQUIRED ⚠️\n\n"
        f"You are about to: {operation}\n"
        f"Details: {details}\n\n"
        f"This operation cannot be undone. To proceed, call this tool again with "
        f"the additional parameter 'confirm=True'.\n\n"
        f"Example: {operation.lower().replace(' ', '_')}(..., confirm=True)"
    )


# ---------------------------------------------------------------------------
# Fleet Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
@rbac_protected("kvm_list_hosts")
async def kvm_list_hosts() -> str:
    """List all configured KVM hosts with their connection status."""
    conn_mgr = _services().connection_manager
    hosts = await asyncio.to_thread(conn_mgr.list_hosts)
    return json.dumps(hosts, indent=2)


@mcp.tool()
@audited_tool
async def kvm_fleet_status() -> str:
    """Get a combined overview of all VMs across every configured host."""
    svc = _services().kvm_service
    conn_mgr = _services().connection_manager
    hosts = await asyncio.to_thread(conn_mgr.list_hosts)

    async def _query(h: dict) -> dict:
        rc, vms = await asyncio.to_thread(svc.list_vms, host=h["name"], all_vms=True)
        return {"host": h["name"], "status": h["status"], "vms": vms if rc == 0 else []}

    results = await asyncio.gather(*[_query(h) for h in hosts])
    return json.dumps(list(results), indent=2)


# ---------------------------------------------------------------------------
# VM Lifecycle Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
async def kvm_list_vms(host: str = "") -> str:
    """List all KVM virtual machines with their IDs and statuses.

    Args:
        host: Target KVM host (empty for default)
    """
    svc = _services().kvm_service
    rc, vms = await asyncio.to_thread(svc.list_vms, host=host, all_vms=True)
    if rc != 0:
        return "Failed to list VMs"
    return json.dumps(vms, indent=2)


@mcp.tool()
@audited_tool
async def kvm_get_vm_status(vm_name: str, host: str = "") -> str:
    """Get the status of a specific VM including running state and IP address.

    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services()
    rc, vm = await asyncio.to_thread(svc.kvm_service.get_vm_status, vm_name, host)
    if rc != 0:
        return "Failed to get VM status"
    if vm is None:
        return f"VM '{vm_name}' not found"

    running = vm["status"] == "running"
    ip_address = None
    if running:
        ip_rc, ip = await asyncio.to_thread(
            svc.guest_agent_service.get_ip_address, vm_name, host,
        )
        if ip_rc == 0:
            ip_address = ip

    result = {**vm, "running": running, "ip_address": ip_address}
    return json.dumps(result, indent=2)


@mcp.tool()
@audited_tool
async def kvm_start_vm(vm_name: str, host: str = "") -> str:
    """Start a stopped virtual machine.

    Args:
        vm_name: Name of the virtual machine to start
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, vm = await asyncio.to_thread(svc.get_vm_status, vm_name, host)
    if rc != 0:
        return "Failed to check VM status"
    if vm is None:
        return f"VM '{vm_name}' not found"
    if vm["status"] == "running":
        return f"VM '{vm_name}' is already running"

    rc, stdout, stderr = await asyncio.to_thread(svc.start_vm, vm_name, host)
    if rc != 0:
        return _format_error("start VM", stderr)
    return f"VM '{vm_name}' started successfully"


@mcp.tool()
@audited_tool
async def kvm_stop_vm(vm_name: str, force: bool = False, host: str = "", confirm: bool = False) -> str:
    """Stop a running virtual machine.

    Args:
        vm_name: Name of the virtual machine to stop
        force: If True, force stop (destroy) the VM instead of graceful shutdown
        host: Target KVM host (empty for default)
        confirm: Set to True to confirm force stop operation
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, vm = await asyncio.to_thread(svc.get_vm_status, vm_name, host)
    if rc != 0:
        return "Failed to check VM status"
    if vm is None:
        return f"VM '{vm_name}' not found"
    if vm["status"] != "running":
        return f"VM '{vm_name}' is not running"

    # Require confirmation for force stop (potential data loss)
    if force and not confirm:
        return _requires_confirmation(
            f"Force stop VM '{vm_name}'",
            f"Force stop may cause data loss or corruption. Use graceful shutdown instead unless VM is unresponsive. "
            f"VM: {vm_name}, Host: {host or 'default'}"
        )

    rc, stdout, stderr = await asyncio.to_thread(svc.stop_vm, vm_name, force, host)
    if rc != 0:
        return _format_error("stop VM", stderr)
    return f"VM '{vm_name}' stopped successfully"


@mcp.tool()
@audited_tool
async def kvm_restart_vm(vm_name: str, force: bool = False, host: str = "") -> str:
    """Restart a virtual machine.

    Args:
        vm_name: Name of the virtual machine to restart
        force: If True, force restart (destroy then start) instead of graceful reboot
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, vm = await asyncio.to_thread(svc.get_vm_status, vm_name, host)
    if rc != 0:
        return "Failed to check VM status"
    if vm is None:
        return f"VM '{vm_name}' not found"

    rc, stdout, stderr = await asyncio.to_thread(svc.restart_vm, vm_name, force, host)
    if rc != 0:
        return _format_error("restart VM", stderr)
    action = "force restarted" if force else "restarted"
    return f"VM '{vm_name}' {action} successfully"


@mcp.tool()
@audited_tool
@rbac_protected("kvm_create_vm")
async def kvm_create_vm(
    name: str,
    memory_mb: int = 2048,
    vcpus: int = 2,
    disk_size_gb: int = 20,
    disk_path: str = "/var/lib/libvirt/images",
    iso_path: Optional[str] = None,
    os_variant: Optional[str] = None,
    network: str = "network=default",
    host: str = "",
) -> str:
    """Create a new virtual machine.

    Args:
        name: Name for the new VM (alphanumeric, dots, underscores, hyphens)
        memory_mb: Memory allocation in MB (512-32768)
        vcpus: Number of virtual CPUs (1-16)
        disk_size_gb: Disk size in GB (10-1000)
        disk_path: Base directory for the disk image
        iso_path: Path to ISO image for OS installation (optional)
        os_variant: OS variant hint for virt-install (e.g. ubuntu24.04)
        network: Network spec passed to virt-install --network (e.g. bridge=br0, network=default)
        host: Target KVM host (empty for default)
    """
    validate_vm_name(name)
    svc = _services().kvm_service

    rc, existing = await asyncio.to_thread(svc.get_vm_status, name, host)
    if rc == 0 and existing is not None:
        return f"VM '{name}' already exists"

    disk_file = f"{disk_path}/{name}.qcow2"
    rc, _, stderr = await asyncio.to_thread(svc.create_vm_disk, disk_file, disk_size_gb, host)
    if rc != 0:
        return _format_error("create disk", stderr)

    rc, _, stderr = await asyncio.to_thread(
        svc.create_vm,
        vm_name=name, memory_mb=memory_mb, vcpus=vcpus,
        disk_path=disk_file, iso_path=iso_path, os_variant=os_variant,
        network=network, host=host,
    )
    if rc != 0:
        return _format_error("create VM", stderr)
    return f"VM '{name}' created successfully"


@mcp.tool()
@audited_tool
@rbac_protected("kvm_delete_vm")
async def kvm_delete_vm(
    vm_name: str, remove_storage: bool = False, host: str = "", confirm: bool = False,
) -> str:
    """Delete a virtual machine. The VM must be stopped first.

    Args:
        vm_name: Name of the virtual machine to delete
        remove_storage: If True, also remove associated disk images
        host: Target KVM host (empty for default)
        confirm: Set to True to confirm destructive operation
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, vm = await asyncio.to_thread(svc.get_vm_status, vm_name, host)
    if rc != 0:
        return "Failed to check VM status"
    if vm is None:
        return f"VM '{vm_name}' not found"
    if vm["status"] == "running":
        return f"VM '{vm_name}' is running -- stop it first"

    # Require confirmation for VM deletion
    if not confirm:
        storage_note = " and all associated disk images" if remove_storage else ""
        return _requires_confirmation(
            f"Delete VM '{vm_name}'{storage_note}",
            f"VM: {vm_name}, Remove storage: {remove_storage}, Host: {host or 'default'}"
        )

    rc, _, stderr = await asyncio.to_thread(svc.delete_vm, vm_name, remove_storage, host)
    if rc != 0:
        return _format_error("delete VM", stderr)
    suffix = " and its storage" if remove_storage else ""
    return f"VM '{vm_name}'{suffix} deleted successfully"


@mcp.tool()
@audited_tool
async def kvm_clone_vm(
    source_vm_name: str,
    target_name: str,
    memory_mb: Optional[int] = None,
    vcpus: Optional[int] = None,
    disk_size_gb: Optional[int] = None,
    host: str = "",
) -> str:
    """Clone an existing VM to create a new one.

    Args:
        source_vm_name: Name of the source VM to clone
        target_name: Name for the new cloned VM
        memory_mb: Override memory in MB (optional)
        vcpus: Override number of vCPUs (optional)
        disk_size_gb: Override disk size in GB (optional)
        host: Target KVM host (empty for default)
    """
    validate_vm_name(source_vm_name)
    validate_vm_name(target_name)
    svc = _services().kvm_service

    rc, _, stderr = await asyncio.to_thread(
        svc.clone_vm,
        source_vm_name=source_vm_name, target_vm_name=target_name,
        memory_mb=memory_mb, vcpus=vcpus, disk_size_gb=disk_size_gb, host=host,
    )
    if rc != 0:
        return _format_error("clone VM", stderr)

    rc, vm = await asyncio.to_thread(svc.get_vm_status, target_name, host)
    if rc == 0 and vm is not None:
        return json.dumps(
            {"message": f"VM '{source_vm_name}' cloned to '{target_name}'", "vm": vm},
            indent=2,
        )
    return f"VM '{source_vm_name}' cloned to '{target_name}'"


# ---------------------------------------------------------------------------
# Boot Order Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
async def kvm_get_boot_order(vm_name: str, host: str = "") -> str:
    """Get the boot device order for a VM.

    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, boot_order = await asyncio.to_thread(svc.get_boot_order, vm_name, host)
    if rc != 0:
        return "Failed to get boot order"
    return json.dumps({"vm": vm_name, "boot_order": boot_order or ""})


@mcp.tool()
@audited_tool
async def kvm_set_boot_order(vm_name: str, boot_order: str, host: str = "") -> str:
    """Set the boot device order for a VM.

    Args:
        vm_name: Name of the virtual machine
        boot_order: Boot order string: 'hd,cdrom', 'cdrom,hd', 'network', or 'fd'
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    valid_orders = {"hd,cdrom", "cdrom,hd", "network", "fd"}
    if boot_order not in valid_orders:
        return (
            f"Invalid boot order '{boot_order}'. "
            f"Must be one of: {', '.join(sorted(valid_orders))}"
        )

    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(svc.set_boot_order, vm_name, boot_order, host)
    if rc != 0:
        return _format_error("set boot order", stderr)
    return f"Boot order for VM '{vm_name}' set to '{boot_order}'"


# ---------------------------------------------------------------------------
# Snapshot Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
async def kvm_create_snapshot(
    vm_name: str, snapshot_name: str, description: Optional[str] = None, host: str = "",
) -> str:
    """Create a snapshot of a virtual machine.

    Args:
        vm_name: Name of the virtual machine
        snapshot_name: Name for the snapshot
        description: Optional description for the snapshot
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    validate_snapshot_name(snapshot_name)
    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(
        svc.create_snapshot, vm_name, snapshot_name, host, description,
    )
    if rc != 0:
        return _format_error("create snapshot", stderr)
    return f"Snapshot '{snapshot_name}' created for VM '{vm_name}'"


@mcp.tool()
@audited_tool
async def kvm_list_snapshots(vm_name: str, host: str = "") -> str:
    """List all snapshots for a virtual machine.

    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, snapshots = await asyncio.to_thread(svc.list_snapshots, vm_name, host)
    if rc != 0:
        return "Failed to list snapshots"
    return json.dumps({"vm": vm_name, "snapshots": snapshots}, indent=2)


@mcp.tool()
@audited_tool
async def kvm_delete_snapshot(vm_name: str, snapshot_name: str, host: str = "") -> str:
    """Delete a snapshot from a virtual machine.

    Args:
        vm_name: Name of the virtual machine
        snapshot_name: Name of the snapshot to delete
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    validate_snapshot_name(snapshot_name)
    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(
        svc.delete_snapshot, vm_name, snapshot_name, host,
    )
    if rc != 0:
        return _format_error("delete snapshot", stderr)
    return f"Snapshot '{snapshot_name}' deleted from VM '{vm_name}'"


@mcp.tool()
@audited_tool
@rbac_protected("kvm_restore_snapshot")
async def kvm_restore_snapshot(vm_name: str, snapshot_name: str, host: str = "", confirm: bool = False) -> str:
    """Restore a VM to a previous snapshot state. The VM must be stopped.

    Args:
        vm_name: Name of the virtual machine
        snapshot_name: Name of the snapshot to restore
        host: Target KVM host (empty for default)
        confirm: Set to True to confirm destructive operation
    """
    validate_vm_name(vm_name)
    validate_snapshot_name(snapshot_name)
    
    # Require confirmation for snapshot restoration (data loss)
    if not confirm:
        return _requires_confirmation(
            f"Restore VM '{vm_name}' to snapshot '{snapshot_name}'",
            f"This will revert all changes made since the snapshot was created. "
            f"VM: {vm_name}, Snapshot: {snapshot_name}, Host: {host or 'default'}"
        )
    
    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(
        svc.restore_snapshot, vm_name, snapshot_name, host,
    )
    if rc != 0:
        return _format_error("restore snapshot", stderr)
    return f"VM '{vm_name}' restored to snapshot '{snapshot_name}'"


# ---------------------------------------------------------------------------
# Disk Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
async def kvm_list_disks(vm_name: str, host: str = "") -> str:
    """List all disks attached to a virtual machine.

    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, disks = await asyncio.to_thread(svc.list_disks, vm_name, host)
    if rc != 0:
        return "Failed to list disks"
    return json.dumps({"vm": vm_name, "disks": disks}, indent=2)


@mcp.tool()
@audited_tool
async def kvm_attach_disk(vm_name: str, disk_path: str, host: str = "") -> str:
    """Attach a disk to a virtual machine.

    Args:
        vm_name: Name of the virtual machine
        disk_path: Path to the disk file (must be under allowed disk paths)
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(svc.attach_disk, vm_name, disk_path, host)
    if rc != 0:
        return _format_error("attach disk", stderr)
    return f"Disk '{disk_path}' attached to VM '{vm_name}'"


@mcp.tool()
@audited_tool
async def kvm_detach_disk(vm_name: str, disk_path: str, host: str = "") -> str:
    """Detach a disk from a virtual machine.

    Args:
        vm_name: Name of the virtual machine
        disk_path: Path or target name of the disk to detach
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(svc.detach_disk, vm_name, disk_path, host)
    if rc != 0:
        return _format_error("detach disk", stderr)
    return f"Disk '{disk_path}' detached from VM '{vm_name}'"


@mcp.tool()
@audited_tool
async def kvm_resize_disk(vm_name: str, new_size_gb: int, host: str = "") -> str:
    """Resize the primary disk of a virtual machine.

    Args:
        vm_name: Name of the virtual machine
        new_size_gb: New disk size in GB (10-1000)
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    if not 10 <= new_size_gb <= 1000:
        return "Disk size must be between 10 and 1000 GB"
    svc = _services().kvm_service

    rc, disks = await asyncio.to_thread(svc.list_disks, vm_name, host)
    if rc != 0 or not disks:
        return f"Failed to find disks for VM '{vm_name}'"
    primary = next((d for d in disks if d.get("type") == "disk"), None)
    if primary is None or not primary.get("source"):
        return f"Could not determine primary disk path for VM '{vm_name}'"

    rc, _, stderr = await asyncio.to_thread(
        svc.resize_disk, primary["source"], new_size_gb, host,
    )
    if rc != 0:
        return _format_error("resize disk", stderr)
    return f"Disk for VM '{vm_name}' resized to {new_size_gb} GB"


# ---------------------------------------------------------------------------
# Network Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
async def kvm_list_networks(host: str = "") -> str:
    """List all available libvirt networks.

    Args:
        host: Target KVM host (empty for default)
    """
    svc = _services().kvm_service
    rc, networks = await asyncio.to_thread(svc.list_networks, host)
    if rc != 0:
        return "Failed to list networks"
    return json.dumps(networks, indent=2)


@mcp.tool()
@audited_tool
async def kvm_attach_network(vm_name: str, network_name: str, host: str = "") -> str:
    """Attach a virtual machine to a libvirt network.

    Args:
        vm_name: Name of the virtual machine
        network_name: Name of the libvirt network to attach
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(
        svc.attach_network, vm_name, network_name, host,
    )
    if rc != 0:
        return _format_error("attach network", stderr)
    return f"VM '{vm_name}' attached to network '{network_name}'"


@mcp.tool()
@audited_tool
async def kvm_detach_network(vm_name: str, network_name: str, host: str = "") -> str:
    """Detach a virtual machine from a libvirt network.

    Args:
        vm_name: Name of the virtual machine
        network_name: Name of the libvirt network to detach
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    rc, _, stderr = await asyncio.to_thread(
        svc.detach_network, vm_name, network_name, host,
    )
    if rc != 0:
        return _format_error("detach network", stderr)
    return f"VM '{vm_name}' detached from network '{network_name}'"


# ---------------------------------------------------------------------------
# Guest Agent Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
async def guest_ping(vm_name: str, host: str = "") -> str:
    """Check if the QEMU guest agent inside a VM is responsive.

    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().guest_agent_service
    rc, response = await asyncio.to_thread(svc.ping, vm_name, host)
    if rc != 0:
        error = response.get("error", "unknown error")
        return f"Guest agent not responding: {error}"
    return f"Guest agent for VM '{vm_name}' is responsive"


@mcp.tool()
@audited_tool
async def guest_get_network(vm_name: str, host: str = "") -> str:
    """Get network interface information from inside the VM via guest agent.

    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().guest_agent_service
    rc, response = await asyncio.to_thread(svc.get_network_interfaces, vm_name, host)
    if rc != 0:
        error = response.get("error", "unknown error")
        return f"Failed to get network interfaces: {error}"
    interfaces = response.get("return", [])
    return json.dumps(interfaces, indent=2)


@mcp.tool()
@audited_tool
async def guest_get_ip(vm_name: str, host: str = "") -> str:
    """Get the primary IP address of a VM via guest agent.

    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().guest_agent_service
    rc, ip = await asyncio.to_thread(svc.get_ip_address, vm_name, host)
    if rc != 0 or ip is None:
        return f"Could not determine IP address for VM '{vm_name}'"
    return json.dumps({"vm": vm_name, "ip_address": ip})


@mcp.tool()
@audited_tool
async def guest_exec(vm_name: str, command: str, host: str = "") -> str:
    """Execute a command inside a VM via the QEMU guest agent.

    Allowlisted commands: ls, pwd, whoami, date, uname, hostname, df, free, ps,
    ip, uptime, head, tail, wc, sort, uniq, which, type, cat, systemctl,
    journalctl, grep, ss, lsblk, lscpu, mount, id, stat, findmnt.
    Shell metacharacters and path traversal are blocked.

    Args:
        vm_name: Name of the virtual machine
        command: Command to execute (e.g. 'uname -a', 'df -h', 'cat /etc/os-release')
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().guest_agent_service
    rc, response = await asyncio.to_thread(svc.execute_command, vm_name, command, host)
    if rc != 0:
        error = response.get("error", "unknown error")
        return f"Failed to execute command: {error}"

    exited = response.get("exited", False)
    if not exited:
        return "Command is still running"

    exit_code = response.get("exitcode")
    stdout_data = response.get("out-data")
    stderr_data = response.get("err-data")

    if stdout_data:
        try:
            stdout_data = base64.b64decode(stdout_data).decode("utf-8", errors="replace")
        except Exception:
            pass
    if stderr_data:
        try:
            stderr_data = base64.b64decode(stderr_data).decode("utf-8", errors="replace")
        except Exception:
            pass

    return json.dumps(
        {"exit_code": exit_code, "stdout": stdout_data, "stderr": stderr_data},
        indent=2,
    )


@mcp.tool()
@audited_tool
async def guest_inject_ssh_key(vm_name: str, public_key: str, host: str = "") -> str:
    """Inject an SSH public key into root's authorized_keys inside a VM.

    The key is validated for format before injection. Supported key types:
    ssh-rsa, ssh-ed25519, ssh-dss, ecdsa-sha2-*.

    Args:
        vm_name: Name of the virtual machine
        public_key: SSH public key string (e.g. 'ssh-ed25519 AAAA... user@host')
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().guest_agent_service
    try:
        rc, response = await asyncio.to_thread(
            svc.setup_ssh_key, vm_name, public_key, host,
        )
    except ValueError as exc:
        return f"Invalid SSH key: {exc}"

    if rc != 0:
        error = response.get("error", "unknown error")
        return f"Failed to inject SSH key: {error}"

    exit_code = response.get("exitcode")
    success = exit_code == 0 if exit_code is not None else True
    if success:
        return f"SSH key injected into VM '{vm_name}' successfully"
    return f"SSH key injection failed (exit code {exit_code})"


@mcp.tool()
@audited_tool
@rbac_protected(operator_or_higher)
async def guest_set_hostname(vm_name: str, hostname: str, host: str = "") -> str:
    """Set the hostname inside a VM via the QEMU guest agent.

    Args:
        vm_name: Name of the virtual machine
        hostname: New hostname (RFC 1123 compliant)
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().guest_agent_service
    rc, response = await asyncio.to_thread(svc.set_hostname, vm_name, hostname, host)
    if rc != 0:
        error = response.get("error", "unknown error")
        return f"Failed to set hostname: {error}"
    exit_code = response.get("exitcode")
    success = exit_code == 0 if exit_code is not None else True
    if success:
        return f"Hostname of VM '{vm_name}' set to '{hostname}'"
    stderr = response.get("err-data", "")
    return f"hostnamectl failed (exit code {exit_code}): {stderr}"


# ---------------------------------------------------------------------------
# MCP Resources (read-only data)
# ---------------------------------------------------------------------------

@mcp.resource("kvm://hosts")
@audited_resource("kvm://hosts")
async def resource_list_hosts() -> str:
    """All configured KVM hosts with connection status."""
    conn_mgr = _services().connection_manager
    hosts = await asyncio.to_thread(conn_mgr.list_hosts)
    return json.dumps(hosts, indent=2)


@mcp.resource("kvm://vms")
@audited_resource("kvm://vms")
async def resource_list_vms() -> str:
    """List of all KVM virtual machines with current statuses."""
    svc = _services().kvm_service
    rc, vms = await asyncio.to_thread(svc.list_vms, all_vms=True)
    if rc != 0:
        return json.dumps({"error": "Failed to list VMs"})
    return json.dumps(vms, indent=2)


@mcp.resource("kvm://networks")
@audited_resource("kvm://networks")
async def resource_list_networks() -> str:
    """List of all available libvirt networks."""
    svc = _services().kvm_service
    rc, networks = await asyncio.to_thread(svc.list_networks)
    if rc != 0:
        return json.dumps({"error": "Failed to list networks"})
    return json.dumps(networks, indent=2)


@mcp.resource("kvm://vms/{vm_name}")
@audited_resource("kvm://vms/{vm_name}")
async def resource_vm_detail(vm_name: str) -> str:
    """Detailed information about a specific VM including status and IP."""
    svc = _services()
    rc, vm = await asyncio.to_thread(svc.kvm_service.get_vm_status, vm_name)
    if rc != 0 or vm is None:
        return json.dumps({"error": f"VM '{vm_name}' not found"})

    running = vm["status"] == "running"
    ip_address = None
    if running:
        ip_rc, ip = await asyncio.to_thread(
            svc.guest_agent_service.get_ip_address, vm_name,
        )
        if ip_rc == 0:
            ip_address = ip

    info_rc, info = await asyncio.to_thread(svc.kvm_service.get_vm_info, vm_name)
    detail = {**vm, "running": running, "ip_address": ip_address}
    if info_rc == 0 and info:
        detail["info"] = info
    return json.dumps(detail, indent=2)


@mcp.resource("kvm://vms/{vm_name}/snapshots")
@audited_resource("kvm://vms/{vm_name}/snapshots")
async def resource_vm_snapshots(vm_name: str) -> str:
    """List of snapshots for a specific VM."""
    svc = _services().kvm_service
    rc, snapshots = await asyncio.to_thread(svc.list_snapshots, vm_name)
    if rc != 0:
        return json.dumps({"error": f"Failed to list snapshots for '{vm_name}'"})
    return json.dumps({"vm": vm_name, "snapshots": snapshots}, indent=2)


@mcp.resource("kvm://vms/{vm_name}/disks")
@audited_resource("kvm://vms/{vm_name}/disks")
async def resource_vm_disks(vm_name: str) -> str:
    """Disk layout for a specific VM (device names, paths, drivers)."""
    svc = _services().kvm_service
    rc, disks = await asyncio.to_thread(svc.list_disks, vm_name)
    if rc != 0:
        return json.dumps({"error": f"Failed to list disks for '{vm_name}'"})
    return json.dumps({"vm": vm_name, "disks": disks}, indent=2)


@mcp.resource("kvm://hosts/{host_name}/vms")
@audited_resource("kvm://hosts/{host_name}/vms")
async def resource_host_vms(host_name: str) -> str:
    """All VMs on a specific host."""
    svc = _services().kvm_service
    rc, vms = await asyncio.to_thread(svc.list_vms, host=host_name, all_vms=True)
    if rc != 0:
        return json.dumps({"error": f"Failed to list VMs on host '{host_name}'"})
    return json.dumps(vms, indent=2)


@mcp.resource("kvm://storage-pools")
@audited_resource("kvm://storage-pools")
async def resource_storage_pools() -> str:
    """Storage pools on the default host with capacity/available space."""
    svc = _services().kvm_service
    rc, pools = await asyncio.to_thread(svc.list_storage_pools)
    if rc != 0:
        return json.dumps({"error": "Failed to list storage pools"})
    return json.dumps(pools, indent=2)


# ---------------------------------------------------------------------------
# MCP Prompts (workflow templates)
# ---------------------------------------------------------------------------

@mcp.prompt()
def provision_vm_from_iso(
    vm_name: str,
    iso_path: str,
    os_variant: str = "generic",
    memory_mb: int = 2048,
    vcpus: int = 2,
    disk_size_gb: int = 20,
) -> str:
    """Guided workflow: create and boot a VM from an ISO image."""
    return (
        f"Provision a new KVM virtual machine with these specs:\n"
        f"- Name: {vm_name}\n"
        f"- ISO: {iso_path}\n"
        f"- OS variant: {os_variant}\n"
        f"- Memory: {memory_mb} MB, vCPUs: {vcpus}, Disk: {disk_size_gb} GB\n\n"
        f"Steps to follow:\n"
        f"1. Call kvm_create_vm with the parameters above and iso_path set.\n"
        f"2. Call kvm_set_boot_order({vm_name}, 'cdrom,hd') so it boots from ISO.\n"
        f"3. Call kvm_start_vm({vm_name}).\n"
        f"4. After OS installation, call kvm_set_boot_order({vm_name}, 'hd,cdrom').\n"
        f"5. Call kvm_restart_vm({vm_name}).\n"
        f"6. Verify with kvm_get_vm_status({vm_name})."
    )


@mcp.prompt()
def clone_and_configure(
    source_vm: str,
    new_name: str,
    ssh_public_key: str = "",
) -> str:
    """Guided workflow: clone a VM and prepare it for SSH access."""
    key_step = (
        f"5. Call guest_inject_ssh_key({new_name}, '<key>') with the SSH public key.\n"
        if ssh_public_key
        else "5. (Optional) Call guest_inject_ssh_key to add an SSH key.\n"
    )
    return (
        f"Clone VM '{source_vm}' to '{new_name}' and configure it:\n\n"
        f"1. Check if '{source_vm}' is running with kvm_get_vm_status. Stop it if needed.\n"
        f"2. Call kvm_clone_vm('{source_vm}', '{new_name}').\n"
        f"3. Call kvm_start_vm('{new_name}').\n"
        f"4. Poll guest_ping('{new_name}') until the guest agent responds.\n"
        f"{key_step}"
        f"6. Call guest_get_ip('{new_name}') to get the IP address.\n"
        f"7. Report the IP so the user can SSH in."
    )


@mcp.prompt()
def snapshot_and_restore(vm_name: str, snapshot_name: str = "backup") -> str:
    """Guided workflow: create a safety snapshot, then restore if needed."""
    return (
        f"Create a safety snapshot of VM '{vm_name}':\n\n"
        f"1. Call kvm_create_snapshot('{vm_name}', '{snapshot_name}').\n"
        f"2. Proceed with whatever changes are needed.\n"
        f"3. If something goes wrong and a rollback is needed:\n"
        f"   a. Call kvm_stop_vm('{vm_name}').\n"
        f"   b. Call kvm_restore_snapshot('{vm_name}', '{snapshot_name}').\n"
        f"   c. Call kvm_start_vm('{vm_name}').\n"
        f"4. Once changes are confirmed good, optionally call "
        f"kvm_delete_snapshot('{vm_name}', '{snapshot_name}') to clean up."
    )


@mcp.prompt()
def investigate_vm(vm_name: str) -> str:
    """Guided workflow: gather diagnostic information about a running VM."""
    return (
        f"Investigate the state of VM '{vm_name}':\n\n"
        f"1. Call kvm_get_vm_status('{vm_name}') for status and IP.\n"
        f"2. Call guest_ping('{vm_name}') to check the guest agent.\n"
        f"3. Call guest_exec('{vm_name}', 'uname -a') for kernel info.\n"
        f"4. Call guest_exec('{vm_name}', 'uptime') for load averages.\n"
        f"5. Call guest_exec('{vm_name}', 'free -m') for memory usage.\n"
        f"6. Call guest_exec('{vm_name}', 'df -h') for disk usage.\n"
        f"7. Call guest_exec('{vm_name}', 'ip -br addr') for network interfaces.\n"
        f"8. Summarize the findings."
    )


@mcp.prompt()
def delete_vm_safely(vm_name: str, remove_storage: bool = True) -> str:
    """Guided workflow: safely stop and delete a VM."""
    storage_note = " and its disk images" if remove_storage else ""
    return (
        f"Safely delete VM '{vm_name}'{storage_note}:\n\n"
        f"1. Call kvm_get_vm_status('{vm_name}') to check current state.\n"
        f"2. If running, call kvm_stop_vm('{vm_name}').\n"
        f"3. If graceful stop fails, call kvm_stop_vm('{vm_name}', force=True).\n"
        f"4. Call kvm_delete_vm('{vm_name}', remove_storage={remove_storage}).\n"
        f"5. Confirm deletion with kvm_list_vms()."
    )


@mcp.prompt()
def fleet_audit() -> str:
    """Guided workflow: audit all KVM hosts for health, orphan VMs, and missing snapshots."""
    return (
        "Audit all configured KVM hosts:\n\n"
        "1. Call kvm_list_hosts() to discover all hosts.\n"
        "2. Call kvm_fleet_status() to get all VMs across every host.\n"
        "3. For each host that is 'disconnected', flag it as needing attention.\n"
        "4. For each running VM, call kvm_list_snapshots(vm_name, host) to check backup coverage.\n"
        "5. Flag any running VM with zero snapshots as 'unprotected'.\n"
        "6. Flag any shut-off VM that has disk images as a candidate for cleanup.\n"
        "7. Read kvm://storage-pools to check available disk space on each host.\n"
        "8. Summarize: host connectivity, VM counts (running/stopped), "
        "unprotected VMs, cleanup candidates, and storage utilization."
    )


@mcp.prompt()
def resize_vm_disk(vm_name: str, new_size_gb: int = 50) -> str:
    """Guided workflow: safely resize a VM's primary disk."""
    return (
        f"Resize the primary disk of VM '{vm_name}' to {new_size_gb} GB:\n\n"
        f"1. Call kvm_get_vm_status('{vm_name}') to check current state.\n"
        f"2. Call kvm_list_disks('{vm_name}') to identify the primary disk path and current size.\n"
        f"3. If the VM is running, call kvm_stop_vm('{vm_name}'). "
        f"If graceful stop fails, use force=True.\n"
        f"4. Call kvm_create_snapshot('{vm_name}', 'pre-resize') as a safety backup.\n"
        f"5. Call kvm_resize_disk('{vm_name}', {new_size_gb}).\n"
        f"6. Call kvm_start_vm('{vm_name}').\n"
        f"7. Wait for guest agent: poll guest_ping('{vm_name}') until responsive.\n"
        f"8. Call guest_exec('{vm_name}', 'lsblk') to confirm the OS sees the new size.\n"
        f"9. Remind the user to grow the filesystem inside the VM if needed "
        f"(e.g. resize2fs, xfs_growfs, or LVM extend)."
    )


@mcp.prompt()
def network_troubleshoot(vm_name: str) -> str:
    """Guided workflow: diagnose network issues on a VM."""
    return (
        f"Diagnose network connectivity for VM '{vm_name}':\n\n"
        f"1. Call kvm_get_vm_status('{vm_name}') — confirm it's running.\n"
        f"2. Read kvm://vms/{vm_name} for detailed info including IP.\n"
        f"3. Call guest_ping('{vm_name}') — if unresponsive, the guest agent "
        f"may be down; suggest checking if qemu-guest-agent is installed.\n"
        f"4. Call guest_get_network('{vm_name}') for interface details.\n"
        f"5. Call guest_exec('{vm_name}', 'ip -br addr') for interface state.\n"
        f"6. Call guest_exec('{vm_name}', 'ip route') to check routing.\n"
        f"7. Call guest_exec('{vm_name}', 'cat /etc/resolv.conf') to check DNS config.\n"
        f"8. Call guest_exec('{vm_name}', 'ss -tlnp') to check listening services.\n"
        f"9. Call kvm_list_networks() to verify the libvirt network is active.\n"
        f"10. Read kvm://vms/{vm_name}/disks to rule out disk-related boot issues.\n"
        f"11. Summarize: interface state, IP assignment, routing, DNS, "
        f"listening services, and libvirt network status."
    )


# ---------------------------------------------------------------------------
# Outcome-Focused Tools (Declarative/Idempotent Operations)
# ---------------------------------------------------------------------------

@mcp.tool()
@audited_tool
async def ensure_vm_running(vm_name: str, host: str = "") -> str:
    """Ensure a VM is in running state (idempotent operation).
    
    If the VM is already running, returns success. If stopped, starts it.
    
    Args:
        vm_name: Name of the virtual machine
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    
    # Check current state
    rc, vm = await asyncio.to_thread(svc.get_vm_status, vm_name, host)
    if rc != 0:
        return "Failed to check VM status"
    if vm is None:
        return f"VM '{vm_name}' not found"
    
    if vm["status"] == "running":
        return f"VM '{vm_name}' is already running"
    
    # Start the VM
    rc, stdout, stderr = await asyncio.to_thread(svc.start_vm, vm_name, host)
    if rc != 0:
        return _format_error("start VM", stderr)
    return f"VM '{vm_name}' is now running"


@mcp.tool()
@audited_tool
async def ensure_vm_stopped(vm_name: str, force: bool = False, host: str = "", confirm: bool = False) -> str:
    """Ensure a VM is in stopped state (idempotent operation).
    
    If the VM is already stopped, returns success. If running, stops it.
    
    Args:
        vm_name: Name of the virtual machine
        force: If True, force stop (destroy) the VM instead of graceful shutdown
        host: Target KVM host (empty for default)
        confirm: Set to True to confirm force stop operation
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    
    # Check current state
    rc, vm = await asyncio.to_thread(svc.get_vm_status, vm_name, host)
    if rc != 0:
        return "Failed to check VM status"
    if vm is None:
        return f"VM '{vm_name}' not found"
    
    if vm["status"] != "running":
        return f"VM '{vm_name}' is already stopped"
    
    # Require confirmation for force stop
    if force and not confirm:
        return _requires_confirmation(
            f"Force stop VM '{vm_name}'",
            f"Force stop may cause data loss or corruption. VM: {vm_name}, Host: {host or 'default'}"
        )
    
    # Stop the VM
    rc, stdout, stderr = await asyncio.to_thread(svc.stop_vm, vm_name, force, host)
    if rc != 0:
        return _format_error("stop VM", stderr)
    return f"VM '{vm_name}' is now stopped"


@mcp.tool()
@audited_tool
async def ensure_vm_exists(
    vm_name: str,
    memory_mb: int = 2048,
    vcpus: int = 2,
    disk_size_gb: int = 20,
    disk_path: str = "/var/lib/libvirt/images",
    iso_path: Optional[str] = None,
    os_variant: Optional[str] = None,
    host: str = "",
) -> str:
    """Ensure a VM exists with specified configuration (idempotent operation).
    
    If the VM already exists, returns success. If not, creates it.
    
    Args:
        vm_name: Name for the VM
        memory_mb: Memory allocation in MB
        vcpus: Number of virtual CPUs
        disk_size_gb: Disk size in GB
        disk_path: Base directory for the disk image
        iso_path: Path to ISO image for OS installation (optional)
        os_variant: OS variant hint for virt-install
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    
    # Check if VM already exists
    rc, vm = await asyncio.to_thread(svc.get_vm_status, vm_name, host)
    if rc == 0 and vm is not None:
        return f"VM '{vm_name}' already exists with status: {vm['status']}"
    
    # Create the VM
    disk_file = f"{disk_path}/{vm_name}.qcow2"
    rc, _, stderr = await asyncio.to_thread(svc.create_vm_disk, disk_file, disk_size_gb, host)
    if rc != 0:
        return _format_error("create disk", stderr)

    rc, _, stderr = await asyncio.to_thread(
        svc.create_vm,
        vm_name=vm_name, memory_mb=memory_mb, vcpus=vcpus,
        disk_path=disk_file, iso_path=iso_path, os_variant=os_variant, host=host,
    )
    if rc != 0:
        return _format_error("create VM", stderr)
    return f"VM '{vm_name}' has been created successfully"


@mcp.tool()
@audited_tool
async def ensure_snapshot_exists(
    vm_name: str, snapshot_name: str, description: Optional[str] = None, host: str = "",
) -> str:
    """Ensure a snapshot exists for a VM (idempotent operation).
    
    If the snapshot already exists, returns success. If not, creates it.
    
    Args:
        vm_name: Name of the virtual machine
        snapshot_name: Name for the snapshot
        description: Optional description for the snapshot
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    validate_snapshot_name(snapshot_name)
    svc = _services().kvm_service
    
    # Check if snapshot already exists
    rc, snapshots = await asyncio.to_thread(svc.list_snapshots, vm_name, host)
    if rc != 0:
        return "Failed to list snapshots"
    
    for snapshot in snapshots:
        if snapshot["name"] == snapshot_name:
            return f"Snapshot '{snapshot_name}' already exists for VM '{vm_name}'"
    
    # Create the snapshot
    rc, _, stderr = await asyncio.to_thread(
        svc.create_snapshot, vm_name, snapshot_name, host, description,
    )
    if rc != 0:
        return _format_error("create snapshot", stderr)
    return f"Snapshot '{snapshot_name}' has been created for VM '{vm_name}'"


@mcp.tool()
@audited_tool
async def ensure_disk_attached(vm_name: str, disk_path: str, host: str = "") -> str:
    """Ensure a disk is attached to a VM (idempotent operation).
    
    If the disk is already attached, returns success. If not, attaches it.
    
    Args:
        vm_name: Name of the virtual machine
        disk_path: Path to the disk file
        host: Target KVM host (empty for default)
    """
    validate_vm_name(vm_name)
    svc = _services().kvm_service
    
    # Check if disk is already attached
    rc, disks = await asyncio.to_thread(svc.list_disks, vm_name, host)
    if rc != 0:
        return "Failed to list VM disks"
    
    for disk in disks:
        if disk.get("source") == disk_path:
            return f"Disk '{disk_path}' is already attached to VM '{vm_name}'"
    
    # Attach the disk
    rc, _, stderr = await asyncio.to_thread(svc.attach_disk, vm_name, disk_path, host)
    if rc != 0:
        return _format_error("attach disk", stderr)
    return f"Disk '{disk_path}' has been attached to VM '{vm_name}'"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from app.transport.http import get_transport_config
    
    # Get transport configuration
    transport_config = get_transport_config()
    
    # Run server with configured transport
    mcp.run(**transport_config)
