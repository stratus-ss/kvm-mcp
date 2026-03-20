"""KVM service for VM lifecycle management using libvirt Python bindings.

Enhanced with security validation inspired by coolnyx/libvirt-mcp-server (MIT License).
"""

import os
import pathlib
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

import libvirt

from app.config import get_settings
from app.services.connection_manager import ConnectionManager
from app.utils.security import PathSecurityValidator, SecurityViolationError

_STATE_MAP = {
    libvirt.VIR_DOMAIN_NOSTATE: "no state",
    libvirt.VIR_DOMAIN_RUNNING: "running",
    libvirt.VIR_DOMAIN_BLOCKED: "blocked",
    libvirt.VIR_DOMAIN_PAUSED: "paused",
    libvirt.VIR_DOMAIN_SHUTDOWN: "shutdown",
    libvirt.VIR_DOMAIN_SHUTOFF: "shut off",
    libvirt.VIR_DOMAIN_CRASHED: "crashed",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "pm-suspended",
}


class VMStatus:
    """VM status constants."""

    RUNNING = "running"
    SHUT_OFF = "shut off"
    PAUSED = "paused"


class KVMService:
    """Service for KVM VM lifecycle operations using libvirt."""

    def __init__(self, conn_mgr: ConnectionManager):
        self._conn_mgr = conn_mgr

    def _validate_disk_path(self, disk_path: str, host: str = "") -> None:
        """Validate disk path is under allowed directories for security.
        
        Enhanced security validation inspired by libvirt-mcp-server.
        """
        host_config = self._conn_mgr.get_host_config(host)
        try:
            PathSecurityValidator.validate_disk_path(disk_path, host_config)
        except SecurityViolationError as e:
            # Convert to ValueError for backward compatibility
            raise ValueError(f"Security violation: {e}") from e

    def _validate_iso_path(self, iso_path: str, host: str = "") -> None:
        """Validate ISO path is under allowed directories for security.
        
        Enhanced security validation inspired by libvirt-mcp-server.
        """
        if not iso_path:
            return  # Optional path
            
        host_config = self._conn_mgr.get_host_config(host)
        try:
            PathSecurityValidator.validate_iso_path(iso_path, host_config)
        except SecurityViolationError as e:
            # Convert to ValueError for backward compatibility
            raise ValueError(f"Security violation: {e}") from e

    def _state_str(self, state_id: int) -> str:
        return _STATE_MAP.get(state_id, "unknown")

    @staticmethod
    def _is_local_uri(uri: str) -> bool:
        return uri.startswith("qemu:///")

    @staticmethod
    def _extract_hostname(uri: str) -> str:
        """Extract hostname from a libvirt URI like qemu+ssh://user@host/system."""
        if "@" in uri:
            after_at = uri.split("@", 1)[1]
            return after_at.split("/", 1)[0].split(":", 1)[0]
        return ""

    def _run_ssh_command(
        self, host: str, command: list[str], timeout: int = 300,
    ) -> tuple[int, str, str]:
        """Run a command locally or via SSH for subprocess-only operations."""
        host = self._conn_mgr.resolve_host(host)
        config = self._conn_mgr.get_host_config(host)
        settings = get_settings()

        if not settings.disable_sudo:
            command = ["sudo"] + command

        if self._is_local_uri(config.uri):
            full_cmd = command
        else:
            hostname = self._extract_hostname(config.uri)
            ssh_key = os.path.expanduser(config.ssh_key)
            cmd_str = shlex.join(command)
            full_cmd = [
                "ssh",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10",
                "-i", ssh_key,
                f"{config.ssh_user}@{hostname}",
                cmd_str,
            ]

        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=timeout, shell=False,
        )
        return result.returncode, result.stdout, result.stderr

    # ── Listing operations ────────────────────────────────────────

    def list_vms(self, host: str = "", all_vms: bool = True) -> tuple[int, list[dict[str, str]]]:
        """List all VMs on a host."""
        try:
            conn = self._conn_mgr.get_connection(host)
            flags = (
                libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE
                | libvirt.VIR_CONNECT_LIST_DOMAINS_INACTIVE
                if all_vms
                else libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE
            )
            vms = []
            for dom in conn.listAllDomains(flags):
                state_id = dom.state()[0]
                dom_id = str(dom.ID()) if dom.ID() >= 0 else "-"
                vms.append({"id": dom_id, "name": dom.name(), "status": self._state_str(state_id)})
            return 0, vms
        except libvirt.libvirtError:
            return 1, []

    def get_vm_status(
        self, vm_name: str, host: str = "",
    ) -> tuple[int, Optional[dict[str, str]]]:
        """Get status of a specific VM."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            state_id = dom.state()[0]
            dom_id = str(dom.ID()) if dom.ID() >= 0 else "-"
            return 0, {"id": dom_id, "name": dom.name(), "status": self._state_str(state_id)}
        except libvirt.libvirtError:
            return 0, None

    def get_vm_info(self, vm_name: str, host: str = "") -> tuple[int, Optional[dict[str, Any]]]:
        """Get detailed VM information."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            info = dom.info()
            return 0, {
                "state": self._state_str(info[0]),
                "max_memory": f"{info[1] // 1024} MiB",
                "used_memory": f"{info[2] // 1024} MiB",
                "vcpus": str(info[3]),
                "cpu_time": f"{info[4] / 1e9:.1f}s",
                "persistent": "yes" if dom.isPersistent() else "no",
                "autostart": "yes" if dom.autostart() else "no",
            }
        except libvirt.libvirtError:
            return 1, None

    def list_disks(self, vm_name: str, host: str = "") -> tuple[int, list[dict[str, str]]]:
        """List disks attached to a VM by parsing domain XML."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            root = ET.fromstring(dom.XMLDesc(0))
            disks = []
            for disk_el in root.findall(".//devices/disk"):
                target = disk_el.find("target")
                source = disk_el.find("source")
                driver = disk_el.find("driver")
                target_dev = target.get("dev", "") if target is not None else ""
                source_file = ""
                if source is not None:
                    source_file = source.get("file", source.get("dev", source.get("volume", "")))
                driver_type = driver.get("type", "") if driver is not None else ""
                disks.append({
                    "name": target_dev,
                    "type": disk_el.get("device", "disk"),
                    "source": source_file,
                    "driver": driver_type,
                })
            return 0, disks
        except libvirt.libvirtError:
            return 1, []

    def list_snapshots(self, vm_name: str, host: str = "") -> tuple[int, list[dict[str, str]]]:
        """List snapshots of a VM."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            result = []
            for snap in dom.listAllSnapshots(0):
                root = ET.fromstring(snap.getXMLDesc(0))
                name = root.findtext("name", "")
                creation = root.findtext("creationTime", "")
                if creation:
                    creation = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(int(creation)),
                    )
                result.append({"name": name, "time": creation})
            return 0, result
        except libvirt.libvirtError:
            return 1, []

    def list_networks(self, host: str = "") -> tuple[int, list[dict[str, str]]]:
        """List all libvirt networks on a host."""
        try:
            conn = self._conn_mgr.get_connection(host)
            result = []
            for net in conn.listAllNetworks(0):
                result.append({
                    "name": net.name(),
                    "state": "active" if net.isActive() else "inactive",
                })
            return 0, result
        except libvirt.libvirtError:
            return 1, []

    def list_storage_pools(self, host: str = "") -> tuple[int, list[dict[str, Any]]]:
        """List all libvirt storage pools with capacity info."""
        try:
            conn = self._conn_mgr.get_connection(host)
            pools = []
            for pool in conn.listAllStoragePools(0):
                info = pool.info()
                pools.append({
                    "name": pool.name(),
                    "state": "active" if pool.isActive() else "inactive",
                    "capacity_gb": round(info[1] / (1024 ** 3), 1),
                    "allocation_gb": round(info[2] / (1024 ** 3), 1),
                    "available_gb": round(info[3] / (1024 ** 3), 1),
                })
            return 0, pools
        except libvirt.libvirtError:
            return 1, []

    # ── Lifecycle operations ──────────────────────────────────────

    def start_vm(self, vm_name: str, host: str = "", timeout: int = 60) -> tuple[int, str, str]:
        """Start a VM."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            dom.create()
            return 0, f"Domain '{vm_name}' started", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def stop_vm(
        self, vm_name: str, force: bool = False, host: str = "", timeout: int = 60,
    ) -> tuple[int, str, str]:
        """Stop a VM (graceful shutdown or force destroy)."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            if force:
                dom.destroy()
            else:
                dom.shutdown()
            action = "destroyed" if force else "shutting down"
            return 0, f"Domain '{vm_name}' {action}", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def restart_vm(
        self, vm_name: str, force: bool = False, host: str = "", timeout: int = 90,
    ) -> tuple[int, str, str]:
        """Restart a VM."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            if force:
                dom.destroy()
                time.sleep(2)
                dom.create()
            else:
                dom.reboot(0)
            action = "force restarted" if force else "rebooting"
            return 0, f"Domain '{vm_name}' {action}", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def delete_vm(
        self, vm_name: str, remove_storage: bool = False, host: str = "", timeout: int = 30,
    ) -> tuple[int, str, str]:
        """Delete (undefine) a VM, optionally removing storage."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            if remove_storage:
                self._delete_vm_with_storage(dom, host)
            else:
                dom.undefine()
            return 0, f"Domain '{vm_name}' undefined", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def _delete_vm_with_storage(self, dom: libvirt.virDomain, host: str) -> None:
        """Undefine a domain and remove its disk volumes."""
        root = ET.fromstring(dom.XMLDesc(0))
        conn = self._conn_mgr.get_connection(host)
        for disk_src in root.findall(".//devices/disk[@device='disk']/source"):
            vol_path = disk_src.get("file")
            if not vol_path:
                continue
            try:
                vol = conn.storageVolLookupByPath(vol_path)
                vol.delete(0)
            except libvirt.libvirtError:
                pass
        flags = (
            libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
            | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
        )
        dom.undefineFlags(flags)

    # ── Boot order operations ─────────────────────────────────────

    def get_boot_order(self, vm_name: str, host: str = "") -> tuple[int, Optional[str]]:
        """Get boot device order from domain XML."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            root = ET.fromstring(dom.XMLDesc(0))
            os_elem = root.find("os")
            if os_elem is None:
                return 0, None
            boot_devs = [b.get("dev") for b in os_elem.findall("boot") if b.get("dev")]
            return 0, ",".join(boot_devs) if boot_devs else None
        except libvirt.libvirtError:
            return 1, None

    def set_boot_order(
        self, vm_name: str, boot_order: str, host: str = "", timeout: int = 30,
    ) -> tuple[int, str, str]:
        """Set boot device order in domain XML."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            conn = self._conn_mgr.get_connection(host)
            root = ET.fromstring(dom.XMLDesc(0))

            os_elem = root.find("os")
            if os_elem is None:
                return 1, "", "No <os> element found in domain XML"

            for boot in os_elem.findall("boot"):
                os_elem.remove(boot)

            type_elem = os_elem.find("type")
            insert_idx = list(os_elem).index(type_elem) + 1 if type_elem is not None else 0
            for i, dev in enumerate(boot_order.split(",")):
                boot_elem = ET.Element("boot")
                boot_elem.set("dev", dev.strip())
                os_elem.insert(insert_idx + i, boot_elem)

            conn.defineXML(ET.tostring(root, encoding="unicode"))
            return 0, f"Boot order set to '{boot_order}'", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    # ── Snapshot operations ───────────────────────────────────────

    def create_snapshot(
        self, vm_name: str, snapshot_name: str, host: str = "",
        description: Optional[str] = None, timeout: int = 600,
    ) -> tuple[int, str, str]:
        """Create a named snapshot."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            desc = f"<description>{description}</description>" if description else ""
            snap_xml = f"<domainsnapshot><name>{snapshot_name}</name>{desc}</domainsnapshot>"
            dom.snapshotCreateXML(snap_xml, 0)
            return 0, f"Snapshot '{snapshot_name}' created", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def delete_snapshot(
        self, vm_name: str, snapshot_name: str, host: str = "", timeout: int = 300,
    ) -> tuple[int, str, str]:
        """Delete a named snapshot."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            snap = dom.snapshotLookupByName(snapshot_name, 0)
            snap.delete(0)
            return 0, f"Snapshot '{snapshot_name}' deleted", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def restore_snapshot(
        self, vm_name: str, snapshot_name: str, host: str = "", timeout: int = 60,
    ) -> tuple[int, str, str]:
        """Revert a VM to a named snapshot. VM must be shut off."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            if dom.state()[0] != libvirt.VIR_DOMAIN_SHUTOFF:
                return 1, "", f"VM '{vm_name}' must be stopped to restore from snapshot"
            snap = dom.snapshotLookupByName(snapshot_name, 0)
            dom.revertToSnapshot(snap, 0)
            return 0, f"Reverted to snapshot '{snapshot_name}'", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    # ── Disk operations ───────────────────────────────────────────

    def attach_disk(
        self, vm_name: str, disk_path: str, host: str = "", timeout: int = 60,
    ) -> tuple[int, str, str]:
        """Attach a qcow2 disk to a VM."""
        try:
            # Validate disk path against security constraints
            self._validate_disk_path(disk_path, host)
            
            dom = self._conn_mgr.get_domain(host, vm_name)
            target = self._next_disk_target(dom)
            if target is None:
                return 1, "", "No available disk targets"
            disk_xml = (
                f'<disk type="file" device="disk">'
                f'<driver name="qemu" type="qcow2"/>'
                f'<source file="{disk_path}"/>'
                f'<target dev="{target}" bus="virtio"/>'
                f'</disk>'
            )
            dom.attachDevice(disk_xml)
            return 0, f"Disk attached as {target}", ""
        except ValueError as e:
            return 1, "", str(e)
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def detach_disk(
        self, vm_name: str, disk_path: str, host: str = "", timeout: int = 60,
    ) -> tuple[int, str, str]:
        """Detach a disk by target device name or source path."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            root = ET.fromstring(dom.XMLDesc(0))
            for disk_el in root.findall(".//devices/disk"):
                target = disk_el.find("target")
                source = disk_el.find("source")
                t_dev = target.get("dev", "") if target is not None else ""
                s_file = ""
                if source is not None:
                    s_file = source.get("file", source.get("dev", ""))
                if t_dev == disk_path or s_file == disk_path:
                    dom.detachDevice(ET.tostring(disk_el, encoding="unicode"))
                    return 0, f"Disk '{disk_path}' detached", ""
            return 1, "", f"Disk '{disk_path}' not found attached to VM"
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def resize_disk(
        self, disk_path: str, new_size_gb: int, host: str = "", timeout: int = 300,
    ) -> tuple[int, str, str]:
        """Resize a qcow2 disk image (subprocess, supports remote via SSH)."""
        try:
            # Validate disk path against security constraints
            self._validate_disk_path(disk_path, host)
        except ValueError as e:
            return 1, "", str(e)
            
        return self._run_ssh_command(
            host, ["qemu-img", "resize", disk_path, f"{new_size_gb}G"], timeout,
        )

    def create_vm_disk(
        self, disk_path: str, size_gb: int, host: str = "", timeout: int = 600,
    ) -> tuple[int, str, str]:
        """Create a new qcow2 disk image (subprocess, supports remote via SSH)."""
        try:
            # Validate disk path against security constraints
            self._validate_disk_path(disk_path, host)
        except ValueError as e:
            return 1, "", str(e)
            
        return self._run_ssh_command(
            host, ["qemu-img", "create", "-f", "qcow2", disk_path, f"{size_gb}G"], timeout,
        )

    def _next_disk_target(self, dom: libvirt.virDomain) -> Optional[str]:
        """Find the next available virtio disk target (vda-vdz)."""
        root = ET.fromstring(dom.XMLDesc(0))
        existing = {
            t.get("dev")
            for t in root.findall(".//devices/disk/target")
            if t.get("dev")
        }
        for i in range(26):
            candidate = f"vd{chr(97 + i)}"
            if candidate not in existing:
                return candidate
        return None

    # ── Network operations ────────────────────────────────────────

    def attach_network(
        self, vm_name: str, network_name: str, host: str = "", timeout: int = 60,
    ) -> tuple[int, str, str]:
        """Attach a VM to a libvirt network."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            iface_xml = (
                f'<interface type="network">'
                f'<source network="{network_name}"/>'
                f'<model type="virtio"/>'
                f'</interface>'
            )
            dom.attachDevice(iface_xml)
            return 0, f"Attached to network '{network_name}'", ""
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    def detach_network(
        self, vm_name: str, network_name: str, host: str = "", timeout: int = 60,
    ) -> tuple[int, str, str]:
        """Detach a VM from a libvirt network (finds interface by network name)."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            root = ET.fromstring(dom.XMLDesc(0))
            for iface in root.findall(".//devices/interface"):
                source = iface.find("source")
                if source is not None and source.get("network") == network_name:
                    dom.detachDevice(ET.tostring(iface, encoding="unicode"))
                    return 0, f"Detached from network '{network_name}'", ""
            return 1, "", f"Network '{network_name}' not found attached to VM"
        except libvirt.libvirtError as e:
            return 1, "", str(e)

    # ── VM creation (subprocess fallback) ─────────────────────────

    def is_vm_running(self, vm_name: str, host: str = "") -> bool:
        rc, vm = self.get_vm_status(vm_name, host)
        return rc == 0 and vm is not None and vm.get("status") == VMStatus.RUNNING

    def wait_for_vm_running(
        self, vm_name: str, host: str = "", timeout: int = 300, check_interval: int = 5,
    ) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.is_vm_running(vm_name, host):
                return True
            time.sleep(check_interval)
        return False

    def create_vm(
        self, vm_name: str, memory_mb: int = 2048, vcpus: int = 2,
        disk_path: Optional[str] = None, iso_path: Optional[str] = None,
        os_variant: Optional[str] = None, network: str = "network=default",
        host: str = "", timeout: int = 600,
    ) -> tuple[int, str, str]:
        """Create a new VM via virt-install (subprocess, supports remote)."""
        try:
            # Validate paths against security constraints
            if disk_path:
                self._validate_disk_path(disk_path, host)
            if iso_path:
                self._validate_iso_path(iso_path, host)
        except ValueError as e:
            return 1, "", str(e)
            
        config = self._conn_mgr.get_host_config(host)
        # Use local URI when command runs on the remote host via SSH
        connect_uri = "qemu:///system" if not self._is_local_uri(config.uri) else config.uri
        cmd = [
            "virt-install",
            "--connect", connect_uri,
            "--name", vm_name,
            "--memory", str(memory_mb),
            "--vcpus", str(vcpus),
            "--disk", f"path={disk_path},format=qcow2",
            "--network", network,
            "--graphics", "vnc",
            "--noautoconsole",
            "--events", "on_reboot=restart",
            "--osinfo", "detect=on,require=off",
            "--channel", "unix,target.type=virtio,target.name=org.qemu.guest_agent.0",
        ]
        if os_variant:
            cmd.extend(["--os-variant", os_variant])
        if iso_path:
            cmd.extend(["--cdrom", iso_path, "--boot", "cdrom,hd"])
        else:
            cmd.append("--import")
        return self._run_ssh_command(host, cmd, timeout)

    def clone_vm(
        self, source_vm_name: str, target_vm_name: str,
        memory_mb: Optional[int] = None, vcpus: Optional[int] = None,
        disk_size_gb: Optional[int] = None, host: str = "", timeout: int = 300,
    ) -> tuple[int, str, str]:
        """Clone a VM via virt-clone (subprocess, supports remote)."""
        rc, vm = self.get_vm_status(source_vm_name, host)
        if rc != 0 or vm is None:
            return rc or 1, "", f"Source VM '{source_vm_name}' not found"
        rc, existing = self.get_vm_status(target_vm_name, host)
        if rc == 0 and existing is not None:
            return 1, "", f"Target VM '{target_vm_name}' already exists"

        config = self._conn_mgr.get_host_config(host)
        # Use local URI when command runs on the remote host via SSH
        connect_uri = "qemu:///system" if not self._is_local_uri(config.uri) else config.uri
        cmd = [
            "virt-clone",
            "--connect", connect_uri,
            "--original", source_vm_name,
            "--name", target_vm_name, "--auto-clone",
        ]
        if memory_mb:
            cmd.extend(["--ram", str(memory_mb)])
        if vcpus:
            cmd.extend(["--vcpu", str(vcpus)])
        if disk_size_gb:
            config = self._conn_mgr.get_host_config(host)
            base_dir = config.allowed_disk_paths.split(",")[0].strip()
            cmd.extend(["--file", f"{base_dir}/{target_vm_name}.qcow2"])
        return self._run_ssh_command(host, cmd, timeout)
