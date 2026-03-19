"""Manages libvirt connections to KVM hosts."""

import logging

import libvirt

from app.config import HostConfig

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Pool of libvirt connections keyed by host name."""

    def __init__(self, hosts: list[HostConfig], default_host: str):
        self._hosts: dict[str, HostConfig] = {h.name: h for h in hosts}
        self._default_host = default_host
        self._connections: dict[str, libvirt.virConnect] = {}

    @property
    def default_host(self) -> str:
        return self._default_host

    def resolve_host(self, host: str) -> str:
        """Return host name, falling back to default if empty."""
        return host if host else self._default_host

    def get_host_config(self, host: str) -> HostConfig:
        """Get host config, resolving empty string to default."""
        host = self.resolve_host(host)
        if host not in self._hosts:
            raise ValueError(f"Unknown host: '{host}'. Available: {list(self._hosts)}")
        return self._hosts[host]

    def get_connection(self, host: str = "") -> libvirt.virConnect:
        """Get or create a libvirt connection for the given host."""
        host = self.resolve_host(host)
        config = self.get_host_config(host)

        existing = self._connections.get(host)
        if existing is not None:
            try:
                existing.getVersion()
                return existing
            except libvirt.libvirtError:
                self._connections.pop(host, None)

        conn = libvirt.open(config.uri)
        if conn is None:
            raise ConnectionError(f"Failed to connect to {config.uri}")
        self._connections[host] = conn
        return conn

    def get_domain(self, host: str, vm_name: str) -> libvirt.virDomain:
        """Look up a domain by name on the specified host."""
        conn = self.get_connection(host)
        return conn.lookupByName(vm_name)

    def list_hosts(self) -> list[dict[str, str]]:
        """Return all configured hosts with connection status."""
        result = []
        for name, config in self._hosts.items():
            status = "unknown"
            try:
                self.get_connection(name)
                status = "connected"
            except Exception:
                status = "disconnected"
            result.append({"name": name, "uri": config.uri, "status": status})
        return result

    def close_all(self) -> None:
        """Close all cached connections."""
        for conn in self._connections.values():
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()
