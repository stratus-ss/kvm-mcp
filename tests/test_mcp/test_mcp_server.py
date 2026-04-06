"""Tests for the KVM MCP server using FastMCP's built-in test methods."""

import json
from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app.mcp_server import mcp


# ── Mock helpers ──────────────────────────────────────────────────────────

MOCK_VMS = [
    {"id": "1", "name": "web-server", "status": "running"},
    {"id": "2", "name": "db-server", "status": "shut off"},
]

MOCK_NETWORKS = [
    {"name": "default", "state": "active"},
    {"name": "isolated", "state": "inactive"},
]

MOCK_SNAPSHOTS = [
    {"name": "before-update", "time": "2026-03-17 10:00:00"},
]

MOCK_DISKS = [
    {"name": "vda", "type": "disk", "source": "/var/lib/libvirt/images/web-server.qcow2", "driver": "qcow2"},
]

MOCK_HOSTS = [
    {"name": "local", "uri": "qemu:///system", "status": "connected"},
]

MOCK_STORAGE_POOLS = [
    {"name": "default", "state": "active", "capacity_gb": 500.0, "allocation_gb": 120.3, "available_gb": 379.7},
]

KVM_SVC = "app.dependencies.Services.kvm_service"
GA_SVC = "app.dependencies.Services.guest_agent_service"
CONN_MGR = "app.dependencies.Services.connection_manager"


def _make_mock():
    return type("Mock", (), {})()


def _apply_kvm_mocks(m):
    m.list_vms = lambda host="", all_vms=True: (0, MOCK_VMS)
    m.get_vm_status = lambda name, host="": next(
        ((0, vm) for vm in MOCK_VMS if vm["name"] == name), (0, None)
    )
    m.start_vm = lambda name, host="", timeout=60: (0, "", "")
    m.stop_vm = lambda name, force=False, host="", timeout=60: (0, "", "")
    m.restart_vm = lambda name, force=False, host="", timeout=90: (0, "", "")
    m.delete_vm = lambda name, remove_storage=False, host="", timeout=30: (0, "", "")
    m.clone_vm = lambda **kw: (0, "", "")
    m.get_boot_order = lambda name, host="": (0, "hd,cdrom")
    m.set_boot_order = lambda name, order, host="", timeout=30: (0, "", "")
    m.create_snapshot = lambda name, snap, host="", description=None, timeout=600: (0, "", "")
    m.list_snapshots = lambda name, host="": (0, MOCK_SNAPSHOTS)
    m.delete_snapshot = lambda name, snap, host="", timeout=300: (0, "", "")
    m.restore_snapshot = lambda name, snap, host="", timeout=60: (0, "", "")
    m.list_disks = lambda name, host="": (0, MOCK_DISKS)
    m.attach_disk = lambda name, path, host="", timeout=60: (0, "", "")
    m.detach_disk = lambda name, path, host="", timeout=60: (0, "", "")
    m.resize_disk = lambda path, size, host="", timeout=300: (0, "", "")
    m.list_networks = lambda host="": (0, MOCK_NETWORKS)
    m.attach_network = lambda name, net, host="", timeout=60: (0, "", "")
    m.detach_network = lambda name, net, host="", timeout=60: (0, "", "")
    m.create_vm_disk = lambda path, size, host="", timeout=600: (0, "", "")
    m.create_vm = lambda **kw: (0, "", "")
    m.get_vm_info = lambda name, host="": (0, {"name": name, "state": "running"})
    m.list_storage_pools = lambda host="": (0, MOCK_STORAGE_POOLS)


def _apply_ga_mocks(m):
    m.ping = lambda name, host="", timeout=10: (0, {"return": {}})
    m.get_network_interfaces = lambda name, host="", timeout=30: (
        0,
        {"return": [{"name": "eth0", "ip-addresses": [{"ip-address": "192.168.1.10"}]}]},
    )
    m.get_ip_address = lambda name, host="", timeout=30: (0, "192.168.1.10")
    m.execute_command = lambda name, cmd, host="", timeout=300: (
        0,
        {"exited": True, "exitcode": 0, "out-data": "dGVzdCBvdXRwdXQ=", "err-data": ""},
    )
    m.setup_ssh_key = lambda name, key, host="", timeout=30, username="root": (0, {"exited": True, "exitcode": 0})


def _apply_conn_mgr_mocks(m):
    m.list_hosts = lambda: MOCK_HOSTS


def _text(result) -> str:
    """Extract text from call_tool result (tuple of content_blocks, structured)."""
    content_blocks = result[0]
    return content_blocks[0].text


# ── Discovery tests ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_lists_all_tools():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert len(names) == 36
    assert "kvm_list_vms" in names
    assert "guest_exec" in names
    assert "kvm_list_hosts" in names
    assert "kvm_fleet_status" in names
    assert "get_tool_metrics" in names
    assert "query_tool_metrics_history" in names
    assert "ensure_vm_running" in names


@pytest.mark.anyio
async def test_lists_static_resources():
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "kvm://vms" in uris
    assert "kvm://networks" in uris
    assert "kvm://hosts" in uris
    assert "kvm://storage-pools" in uris


@pytest.mark.anyio
async def test_lists_resource_templates():
    templates = await mcp.list_resource_templates()
    uris = {t.uriTemplate for t in templates}
    assert "kvm://vms/{vm_name}" in uris
    assert "kvm://vms/{vm_name}/snapshots" in uris
    assert "kvm://vms/{vm_name}/disks" in uris
    assert "kvm://hosts/{host_name}/vms" in uris


@pytest.mark.anyio
async def test_lists_all_8_prompts():
    prompts = await mcp.list_prompts()
    names = {p.name for p in prompts}
    assert names == {
        "provision_vm_from_iso",
        "clone_and_configure",
        "snapshot_and_restore",
        "investigate_vm",
        "delete_vm_safely",
        "fleet_audit",
        "resize_vm_disk",
        "network_troubleshoot",
    }


# ── Fleet tool tests ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_hosts():
    cm = _make_mock()
    _apply_conn_mgr_mocks(cm)
    with patch(CONN_MGR, cm):
        result = await mcp.call_tool("kvm_list_hosts", {})
        data = json.loads(_text(result))
        assert len(data) == 1
        assert data[0]["name"] == "local"


@pytest.mark.anyio
async def test_fleet_status():
    kvm = _make_mock()
    cm = _make_mock()
    _apply_kvm_mocks(kvm)
    _apply_conn_mgr_mocks(cm)
    with patch(KVM_SVC, kvm), patch(CONN_MGR, cm):
        result = await mcp.call_tool("kvm_fleet_status", {})
        data = json.loads(_text(result))
        assert len(data) == 1
        assert data[0]["host"] == "local"
        assert len(data[0]["vms"]) == 2


# ── VM lifecycle tool tests ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_vms():
    mock = _make_mock()
    _apply_kvm_mocks(mock)
    with patch(KVM_SVC, mock):
        result = await mcp.call_tool("kvm_list_vms", {})
        data = json.loads(_text(result))
        assert len(data) == 2
        assert data[0]["name"] == "web-server"


@pytest.mark.anyio
async def test_get_vm_status_running():
    kvm = _make_mock()
    ga = _make_mock()
    _apply_kvm_mocks(kvm)
    _apply_ga_mocks(ga)
    with patch(KVM_SVC, kvm), patch(GA_SVC, ga):
        result = await mcp.call_tool("kvm_get_vm_status", {"vm_name": "web-server"})
        data = json.loads(_text(result))
        assert data["running"] is True
        assert data["ip_address"] == "192.168.1.10"


@pytest.mark.anyio
async def test_get_vm_status_not_found():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_get_vm_status", {"vm_name": "ghost"})
        assert "not found" in _text(result)


@pytest.mark.anyio
async def test_start_vm():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_start_vm", {"vm_name": "db-server"})
        assert "started successfully" in _text(result)


@pytest.mark.anyio
async def test_start_vm_already_running():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_start_vm", {"vm_name": "web-server"})
        assert "already running" in _text(result)


@pytest.mark.anyio
async def test_stop_vm():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_stop_vm", {"vm_name": "web-server"})
        assert "stopped successfully" in _text(result)


@pytest.mark.anyio
async def test_stop_vm_not_running():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_stop_vm", {"vm_name": "db-server"})
        assert "not running" in _text(result)


@pytest.mark.anyio
async def test_restart_vm():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_restart_vm", {"vm_name": "web-server"})
        assert "restarted successfully" in _text(result)


@pytest.mark.anyio
async def test_delete_vm_requires_confirmation():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_delete_vm", {"vm_name": "db-server"})
        assert "CONFIRMATION REQUIRED" in _text(result)


@pytest.mark.anyio
async def test_delete_vm_confirmed():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_delete_vm", {"vm_name": "db-server", "confirm": True})
        assert "deleted successfully" in _text(result)


@pytest.mark.anyio
async def test_delete_vm_blocked_when_running():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_delete_vm", {"vm_name": "web-server"})
        assert "stop it first" in _text(result)


@pytest.mark.anyio
async def test_clone_vm():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_clone_vm",
            {"source_vm_name": "web-server", "target_name": "web-clone"},
        )
        assert "cloned" in _text(result)


@pytest.mark.anyio
async def test_create_vm():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    kvm.get_vm_status = lambda name, host="": (0, None)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_create_vm", {"name": "new-vm"})
        assert "created successfully" in _text(result)


# ── Boot order tests ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_boot_order():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_get_boot_order", {"vm_name": "web-server"})
        data = json.loads(_text(result))
        assert data["boot_order"] == "hd,cdrom"


@pytest.mark.anyio
async def test_set_boot_order():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_set_boot_order", {"vm_name": "web-server", "boot_order": "cdrom,hd"}
        )
        assert "set to" in _text(result)


@pytest.mark.anyio
async def test_set_boot_order_rejects_invalid():
    result = await mcp.call_tool(
        "kvm_set_boot_order", {"vm_name": "web-server", "boot_order": "usb"}
    )
    assert "Invalid boot order" in _text(result)


# ── Snapshot tests ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_snapshot():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_create_snapshot", {"vm_name": "web-server", "snapshot_name": "snap1"}
        )
        assert "created" in _text(result)


@pytest.mark.anyio
async def test_list_snapshots():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_list_snapshots", {"vm_name": "web-server"})
        data = json.loads(_text(result))
        assert len(data["snapshots"]) == 1


@pytest.mark.anyio
async def test_delete_snapshot():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_delete_snapshot", {"vm_name": "web-server", "snapshot_name": "snap1"}
        )
        assert "deleted" in _text(result)


@pytest.mark.anyio
async def test_restore_snapshot_requires_confirmation():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_restore_snapshot", {"vm_name": "db-server", "snapshot_name": "snap1"}
        )
        assert "CONFIRMATION REQUIRED" in _text(result)


@pytest.mark.anyio
async def test_restore_snapshot_confirmed():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_restore_snapshot",
            {"vm_name": "db-server", "snapshot_name": "snap1", "confirm": True},
        )
        assert "restored" in _text(result)


# ── Disk tests ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_disks():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_list_disks", {"vm_name": "web-server"})
        data = json.loads(_text(result))
        assert len(data["disks"]) == 1


@pytest.mark.anyio
async def test_attach_disk():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_attach_disk",
            {"vm_name": "web-server", "disk_path": "/var/lib/libvirt/images/extra.qcow2"},
        )
        assert "attached" in _text(result)


@pytest.mark.anyio
async def test_resize_disk():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_resize_disk", {"vm_name": "web-server", "new_size_gb": 50}
        )
        assert "resized" in _text(result)


@pytest.mark.anyio
async def test_resize_disk_rejects_invalid_size():
    result = await mcp.call_tool(
        "kvm_resize_disk", {"vm_name": "web-server", "new_size_gb": 5}
    )
    assert "between 10 and 1000" in _text(result)


# ── Network tests ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_networks():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_list_networks", {})
        data = json.loads(_text(result))
        assert len(data) == 2


@pytest.mark.anyio
async def test_attach_network():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_attach_network", {"vm_name": "web-server", "network_name": "default"}
        )
        assert "attached" in _text(result)


@pytest.mark.anyio
async def test_detach_network():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool(
            "kvm_detach_network", {"vm_name": "web-server", "network_name": "default"}
        )
        assert "detached" in _text(result)


# ── Guest agent tests ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_guest_ping():
    ga = _make_mock()
    _apply_ga_mocks(ga)
    with patch(GA_SVC, ga):
        result = await mcp.call_tool("guest_ping", {"vm_name": "web-server"})
        assert "responsive" in _text(result)


@pytest.mark.anyio
async def test_guest_get_network():
    ga = _make_mock()
    _apply_ga_mocks(ga)
    with patch(GA_SVC, ga):
        result = await mcp.call_tool("guest_get_network", {"vm_name": "web-server"})
        data = json.loads(_text(result))
        assert data[0]["name"] == "eth0"


@pytest.mark.anyio
async def test_guest_get_ip():
    ga = _make_mock()
    _apply_ga_mocks(ga)
    with patch(GA_SVC, ga):
        result = await mcp.call_tool("guest_get_ip", {"vm_name": "web-server"})
        data = json.loads(_text(result))
        assert data["ip_address"] == "192.168.1.10"


@pytest.mark.anyio
async def test_guest_exec():
    ga = _make_mock()
    _apply_ga_mocks(ga)
    with patch(GA_SVC, ga):
        result = await mcp.call_tool(
            "guest_exec", {"vm_name": "web-server", "command": "uname -a"}
        )
        data = json.loads(_text(result))
        assert data["exit_code"] == 0
        assert data["stdout"] == "test output"


@pytest.mark.anyio
async def test_guest_inject_ssh_key():
    ga = _make_mock()
    _apply_ga_mocks(ga)
    with patch(GA_SVC, ga):
        result = await mcp.call_tool(
            "guest_inject_ssh_key",
            {
                "vm_name": "web-server",
                "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest user@host",
            },
        )
        assert "successfully" in _text(result)


# ── Validation tests ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_invalid_vm_name_rejected():
    with pytest.raises(ToolError, match="Invalid VM name"):
        await mcp.call_tool("kvm_start_vm", {"vm_name": "bad name!"})


@pytest.mark.anyio
async def test_invalid_snapshot_name_rejected():
    with pytest.raises(ToolError, match="Invalid snapshot name"):
        await mcp.call_tool(
            "kvm_create_snapshot",
            {"vm_name": "web-server", "snapshot_name": "bad snap!"},
        )


# ── Resource tests ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_resource_hosts():
    cm = _make_mock()
    _apply_conn_mgr_mocks(cm)
    with patch(CONN_MGR, cm):
        result = await mcp.read_resource("kvm://hosts")
        data = json.loads(result[0].content)
        assert len(data) == 1
        assert data[0]["name"] == "local"


@pytest.mark.anyio
async def test_resource_vms():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.read_resource("kvm://vms")
        data = json.loads(result[0].content)
        assert len(data) == 2


@pytest.mark.anyio
async def test_resource_networks():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.read_resource("kvm://networks")
        data = json.loads(result[0].content)
        assert len(data) == 2


@pytest.mark.anyio
async def test_resource_vm_detail():
    kvm = _make_mock()
    ga = _make_mock()
    _apply_kvm_mocks(kvm)
    _apply_ga_mocks(ga)
    with patch(KVM_SVC, kvm), patch(GA_SVC, ga):
        result = await mcp.read_resource("kvm://vms/web-server")
        data = json.loads(result[0].content)
        assert data["name"] == "web-server"
        assert data["running"] is True


@pytest.mark.anyio
async def test_resource_vm_snapshots():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.read_resource("kvm://vms/web-server/snapshots")
        data = json.loads(result[0].content)
        assert len(data["snapshots"]) == 1


# ── Prompt tests ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_prompt_provision_vm():
    result = await mcp.get_prompt(
        "provision_vm_from_iso",
        arguments={"vm_name": "test-vm", "iso_path": "/iso/ubuntu.iso"},
    )
    text = result.messages[0].content.text
    assert "test-vm" in text
    assert "kvm_create_vm" in text


@pytest.mark.anyio
async def test_prompt_clone_and_configure():
    result = await mcp.get_prompt(
        "clone_and_configure",
        arguments={"source_vm": "web-server", "new_name": "web-clone"},
    )
    text = result.messages[0].content.text
    assert "kvm_clone_vm" in text


@pytest.mark.anyio
async def test_prompt_investigate_vm():
    result = await mcp.get_prompt(
        "investigate_vm",
        arguments={"vm_name": "web-server"},
    )
    text = result.messages[0].content.text
    assert "guest_exec" in text
    assert "df -h" in text


@pytest.mark.anyio
async def test_prompt_delete_vm_safely():
    result = await mcp.get_prompt(
        "delete_vm_safely",
        arguments={"vm_name": "old-vm"},
    )
    text = result.messages[0].content.text
    assert "kvm_delete_vm" in text


@pytest.mark.anyio
async def test_prompt_snapshot_and_restore():
    result = await mcp.get_prompt(
        "snapshot_and_restore",
        arguments={"vm_name": "web-server", "snapshot_name": "safe-point"},
    )
    text = result.messages[0].content.text
    assert "kvm_create_snapshot" in text
    assert "safe-point" in text


@pytest.mark.anyio
async def test_prompt_fleet_audit():
    result = await mcp.get_prompt("fleet_audit", arguments={})
    text = result.messages[0].content.text
    assert "kvm_list_hosts" in text
    assert "kvm_fleet_status" in text
    assert "storage-pools" in text


@pytest.mark.anyio
async def test_prompt_resize_vm_disk():
    result = await mcp.get_prompt(
        "resize_vm_disk",
        arguments={"vm_name": "web-server", "new_size_gb": "100"},
    )
    text = result.messages[0].content.text
    assert "kvm_resize_disk" in text
    assert "pre-resize" in text
    assert "lsblk" in text


@pytest.mark.anyio
async def test_prompt_network_troubleshoot():
    result = await mcp.get_prompt(
        "network_troubleshoot",
        arguments={"vm_name": "web-server"},
    )
    text = result.messages[0].content.text
    assert "guest_ping" in text
    assert "ip route" in text
    assert "resolv.conf" in text
    assert "ss -tlnp" in text


# ── New resource tests ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_resource_vm_disks():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.read_resource("kvm://vms/web-server/disks")
        data = json.loads(result[0].content)
        assert data["vm"] == "web-server"
        assert len(data["disks"]) == 1
        assert data["disks"][0]["name"] == "vda"


@pytest.mark.anyio
async def test_resource_host_vms():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.read_resource("kvm://hosts/local/vms")
        data = json.loads(result[0].content)
        assert len(data) == 2
        assert data[0]["name"] == "web-server"


@pytest.mark.anyio
async def test_resource_storage_pools():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.read_resource("kvm://storage-pools")
        data = json.loads(result[0].content)
        assert len(data) == 1
        assert data[0]["name"] == "default"
        assert data[0]["available_gb"] > 0


# ── Fleet summary tests ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_fleet_status_summary():
    kvm = _make_mock()
    cm = _make_mock()
    _apply_kvm_mocks(kvm)
    _apply_conn_mgr_mocks(cm)
    with patch(KVM_SVC, kvm), patch(CONN_MGR, cm):
        result = await mcp.call_tool("kvm_fleet_status", {"summary": True})
        data = json.loads(_text(result))
        assert data[0]["running"] == 1
        assert data[0]["stopped"] == 1
        assert data[0]["total"] == 2
        assert "vms" not in data[0]


# ── List VMs filter tests ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_vms_name_filter():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_list_vms", {"name_filter": "web"})
        data = json.loads(_text(result))
        assert len(data) == 1
        assert data[0]["name"] == "web-server"


@pytest.mark.anyio
async def test_list_vms_status_filter():
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_list_vms", {"status_filter": "running"})
        data = json.loads(_text(result))
        assert len(data) == 1
        assert data[0]["status"] == "running"


# ── Guest exec truncation tests ──────────────────────────────────────────

@pytest.mark.anyio
async def test_guest_exec_truncation():
    ga = _make_mock()
    import base64
    long_output = "\n".join(f"line {i}" for i in range(200))
    encoded = base64.b64encode(long_output.encode()).decode()
    ga.execute_command = lambda name, cmd, host="", timeout=300: (
        0, {"exited": True, "exitcode": 0, "out-data": encoded, "err-data": ""},
    )
    with patch(GA_SVC, ga):
        result = await mcp.call_tool(
            "guest_exec", {"vm_name": "web-server", "command": "ps aux", "max_lines": 10}
        )
        data = json.loads(_text(result))
        assert data["truncated"] is True
        assert "190 lines truncated" in data["stdout"]


# ── Compact JSON tests ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_responses_are_compact_json():
    """Verify JSON responses contain no pretty-print whitespace."""
    kvm = _make_mock()
    _apply_kvm_mocks(kvm)
    with patch(KVM_SVC, kvm):
        result = await mcp.call_tool("kvm_list_vms", {})
        text = _text(result)
        assert "\n" not in text
        assert "  " not in text


# ── Metrics tool tests ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_tool_metrics():
    result = await mcp.call_tool("get_tool_metrics", {"limit": 5})
    data = json.loads(_text(result))
    assert "records" in data
    assert "summary" in data
    assert data["limit"] == 5


@pytest.mark.anyio
async def test_query_tool_metrics_history():
    result = await mcp.call_tool("query_tool_metrics_history", {"limit": 10})
    data = json.loads(_text(result))
    assert "records" in data
    assert "summary" in data
    assert "filters" in data
