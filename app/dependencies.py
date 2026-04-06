"""Dependency injection container for service layer."""

from app.config import get_settings, load_host_configs, resolve_hosts_file_path
from app.services.connection_manager import ConnectionManager
from app.services.guest_agent_service import GuestAgentService
from app.services.kvm_service import KVMService


class Services:
    """Singleton container that lazily creates services sharing one ConnectionManager."""

    def __init__(self):
        self._conn_mgr: ConnectionManager | None = None
        self._kvm_service: KVMService | None = None
        self._guest_agent_service: GuestAgentService | None = None

    @property
    def connection_manager(self) -> ConnectionManager:
        if self._conn_mgr is None:
            settings = get_settings()
            hosts, default = load_host_configs(settings)
            hosts_file = resolve_hosts_file_path(settings)
            self._conn_mgr = ConnectionManager(hosts, default, hosts_file)
        return self._conn_mgr

    @property
    def kvm_service(self) -> KVMService:
        if self._kvm_service is None:
            self._kvm_service = KVMService(self.connection_manager)
        return self._kvm_service

    @property
    def guest_agent_service(self) -> GuestAgentService:
        if self._guest_agent_service is None:
            self._guest_agent_service = GuestAgentService(self.connection_manager)
        return self._guest_agent_service

    def close(self) -> None:
        """Close all libvirt connections."""
        if self._conn_mgr is not None:
            self._conn_mgr.close_all()


_services: Services | None = None


def get_services() -> Services:
    """Get singleton services instance."""
    global _services
    if _services is None:
        _services = Services()
    return _services
