"""QEMU Guest Agent service using libvirt Python bindings."""

import base64
import json
import re
import time
from typing import Any, Optional

import libvirt
import libvirt_qemu

from app.services.connection_manager import ConnectionManager

SHELL_METACHAR_PATTERN = re.compile(r'[|&;`$()><\n\r]')

ALLOWED_COMMANDS = frozenset([
    "ls", "pwd", "whoami", "date", "uname", "hostname",
    "df", "free", "ps", "ip", "uptime", "head", "tail", "wc",
    "sort", "uniq", "which", "type",
    "cat", "systemctl", "journalctl", "grep",
    "ss", "lsblk", "lscpu", "mount", "id", "stat", "findmnt",
])


class GuestAgentService:
    """Service for QEMU guest agent operations via libvirt."""

    def __init__(self, conn_mgr: ConnectionManager):
        self._conn_mgr = conn_mgr

    def _validate_command(self, command: str) -> tuple[bool, Optional[str]]:
        """Validate a command against the allowlist and safety rules."""
        command = command.strip()
        if not command:
            return False, "Empty command"
        if SHELL_METACHAR_PATTERN.search(command):
            return False, "Shell metacharacters are not allowed"
        cmd_parts = command.split()
        if cmd_parts[0] not in ALLOWED_COMMANDS:
            return False, f"Command not allowed: {cmd_parts[0]}"
        for token in cmd_parts[1:]:
            if ".." in token:
                return False, "Path traversal (..) is not allowed"
        return True, None

    def _agent_command(
        self, vm_name: str, command: dict[str, Any], host: str = "", timeout: int = 30,
    ) -> tuple[int, dict[str, Any]]:
        """Execute a guest agent command via libvirt_qemu."""
        try:
            dom = self._conn_mgr.get_domain(host, vm_name)
            result_str = libvirt_qemu.qemuAgentCommand(
                dom, json.dumps(command), timeout, 0,
            )
            return 0, json.loads(result_str)
        except libvirt.libvirtError as e:
            return 1, {"error": str(e)}
        except json.JSONDecodeError as e:
            return 1, {"error": f"Invalid JSON response: {e}"}

    def ping(
        self, vm_name: str, host: str = "", timeout: int = 10,
    ) -> tuple[int, dict[str, Any]]:
        """Check if the guest agent is responsive."""
        return self._agent_command(vm_name, {"execute": "guest-ping"}, host, timeout)

    def get_network_interfaces(
        self, vm_name: str, host: str = "", timeout: int = 30,
    ) -> tuple[int, dict[str, Any]]:
        """Get network interfaces from inside the guest."""
        return self._agent_command(
            vm_name, {"execute": "guest-network-get-interfaces"}, host, timeout,
        )

    def get_ip_address(
        self, vm_name: str, host: str = "", timeout: int = 30,
    ) -> tuple[int, Optional[str]]:
        """Get the primary non-loopback IP address of the VM."""
        rc, response = self.get_network_interfaces(vm_name, host, timeout)
        if rc != 0:
            return rc, None
        try:
            for iface in response.get("return", []):
                if iface.get("name") != "lo":
                    addrs = iface.get("ip-addresses", [])
                    if addrs:
                        return 0, addrs[0].get("ip-address")
            return 1, None
        except (KeyError, IndexError, TypeError):
            return 1, None

    def get_guest_info(
        self, vm_name: str, host: str = "", timeout: int = 30,
    ) -> tuple[int, dict[str, Any]]:
        """Get guest agent version information."""
        return self._agent_command(vm_name, {"execute": "guest-info"}, host, timeout)

    def execute_command(
        self, vm_name: str, command: str, host: str = "", timeout: int = 300,
    ) -> tuple[int, dict[str, Any]]:
        """Execute an allowlisted command inside the guest."""
        is_valid, error = self._validate_command(command)
        if not is_valid:
            return 1, {"error": error}

        ga_cmd = {
            "execute": "guest-exec",
            "arguments": {
                "path": "/bin/bash",
                "arg": ["-c", command],
                "capture-output": True,
            },
        }
        rc, response = self._agent_command(vm_name, ga_cmd, host, timeout)
        if rc != 0:
            return rc, response

        pid = response.get("return", {}).get("pid")
        if pid is not None:
            return self._get_exec_result(vm_name, pid, host, timeout)
        return rc, response

    def _get_exec_result(
        self, vm_name: str, pid: int, host: str = "", timeout: int = 300,
    ) -> tuple[int, dict[str, Any]]:
        """Poll guest-exec-status until the command finishes."""
        start = time.time()
        while time.time() - start < timeout:
            rc, response = self._agent_command(
                vm_name,
                {"execute": "guest-exec-status", "arguments": {"pid": pid}},
                host, 10,
            )
            if rc != 0:
                return rc, response
            result = response.get("return", {})
            if result.get("exited", False):
                return 0, result
            time.sleep(1)
        return 1, {"error": "Command execution timeout"}

    @staticmethod
    def _validate_ssh_public_key(public_key: str) -> None:
        """Raise ValueError if public_key doesn't look like a valid SSH public key."""
        valid_prefixes = (
            "ssh-rsa", "ssh-ed25519", "ssh-dss",
            "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
        )
        stripped = public_key.strip()
        if not stripped:
            raise ValueError("Public key is empty")
        if SHELL_METACHAR_PATTERN.search(stripped):
            raise ValueError("Public key contains shell metacharacters")
        if stripped.split()[0] not in valid_prefixes:
            raise ValueError(
                f"Public key must start with one of: {', '.join(valid_prefixes)}"
            )

    def set_hostname(
        self, vm_name: str, hostname: str, host: str = "", timeout: int = 30,
    ) -> tuple[int, dict[str, Any]]:
        """Set the hostname inside the guest via hostnamectl."""
        if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$', hostname):
            return 1, {"error": "Invalid hostname"}
        ga_cmd = {
            "execute": "guest-exec",
            "arguments": {
                "path": "/usr/bin/hostnamectl",
                "arg": ["set-hostname", hostname],
                "capture-output": True,
            },
        }
        rc, response = self._agent_command(vm_name, ga_cmd, host, timeout)
        if rc != 0:
            return rc, response
        pid = response.get("return", {}).get("pid")
        if pid is not None:
            return self._get_exec_result(vm_name, pid, host, timeout)
        return rc, response

    def setup_ssh_key(
        self,
        vm_name: str,
        public_key: str,
        host: str = "",
        timeout: int = 30,
        username: str = "root",
    ) -> tuple[int, dict[str, Any]]:
        """Inject an SSH public key into the specified user's authorized_keys via guest agent."""
        self._validate_ssh_public_key(public_key)
        if not username or "/" in username or username in (".", ".."):
            raise ValueError(f"Invalid username: {username!r}")
        home_dir = "/root" if username == "root" else f"/home/{username}"
        encoded_key = base64.b64encode((public_key.strip() + "\n").encode()).decode()
        ssh_dir = f"{home_dir}/.ssh"
        auth_keys = f"{ssh_dir}/authorized_keys"
        shell_cmd = (
            f"mkdir -p {ssh_dir} && chmod 700 {ssh_dir} "
            f"&& cat >> {auth_keys} "
            f"&& chmod 600 {auth_keys}"
        )
        ga_cmd = {
            "execute": "guest-exec",
            "arguments": {
                "path": "/bin/sh",
                "arg": ["-c", shell_cmd],
                "input-data": encoded_key,
                "capture-output": True,
            },
        }
        rc, response = self._agent_command(vm_name, ga_cmd, host, timeout)
        if rc != 0:
            return rc, response
        pid = response.get("return", {}).get("pid")
        if pid is not None:
            return self._get_exec_result(vm_name, pid, host, timeout)
        return rc, response
