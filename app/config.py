"""Configuration management for KVM MCP server.

Includes RBAC configuration adapted from coolnyx/libvirt-mcp-server (MIT License).
See: https://github.com/coolnyx/libvirt-mcp-server
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class HostConfig(BaseModel):
    """Configuration for a single KVM host."""

    name: str
    uri: str = "qemu:///system"
    ssh_user: str = "root"
    ssh_key: str = "~/.ssh/id_rsa"
    allowed_disk_paths: str = "/var/lib/libvirt/images"
    allowed_iso_paths: str = "/var/lib/libvirt/images,/home"


class SecurityConfig(BaseModel):
    """Security and RBAC configuration.
    
    Based on configuration structure from coolnyx/libvirt-mcp-server (MIT License).
    """

    auth_required: bool = Field(
        default=False, 
        description="Whether authentication is required for MCP operations"
    )
    max_concurrent_ops: int = Field(
        default=20, 
        description="Maximum number of concurrent operations allowed"
    )
    allowed_operations: list[str] = Field(
        default=["*"], 
        description="List of allowed operation patterns. Use '*' for all, or specific patterns like 'kvm_*', 'guest_*'"
    )
    rate_limit_per_minute: int = Field(
        default=300, 
        description="Maximum operations per minute per client"
    )


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    kvm_hosts_file: str = ""
    kvm_host: str = ""
    kvm_host_user: str = "root"
    kvm_host_ssh_key: str = "~/.ssh/id_rsa"

    allowed_disk_paths: str = "/var/lib/libvirt/images"
    allowed_iso_paths: str = "/var/lib/libvirt/images,/home"

    log_level: str = "INFO"
    log_format: str = "json"
    disable_sudo: bool = False
    
    # Security and RBAC settings
    security_auth_required: bool = False
    security_max_concurrent_ops: int = 20
    security_allowed_operations: str = "*"
    security_rate_limit_per_minute: int = 300

    @property
    def security(self) -> SecurityConfig:
        """Get security configuration from environment variables."""
        return SecurityConfig(
            auth_required=self.security_auth_required,
            max_concurrent_ops=self.security_max_concurrent_ops,
            allowed_operations=self.security_allowed_operations.split(",") if self.security_allowed_operations != "*" else ["*"],
            rate_limit_per_minute=self.security_rate_limit_per_minute,
        )


def load_host_configs(settings: Settings) -> tuple[list[HostConfig], str]:
    """Resolve host configurations. Returns (hosts, default_host_name)."""
    hosts_file = settings.kvm_hosts_file
    if hosts_file and Path(hosts_file).is_file():
        with open(hosts_file) as f:
            data = yaml.safe_load(f) or {}
        hosts = [HostConfig(**h) for h in data.get("hosts", [])]
        default = data.get("default_host", hosts[0].name if hosts else "local")
        if hosts:
            return hosts, default

    if settings.kvm_host:
        host = HostConfig(
            name=settings.kvm_host,
            uri=f"qemu+ssh://{settings.kvm_host_user}@{settings.kvm_host}/system",
            ssh_user=settings.kvm_host_user,
            ssh_key=settings.kvm_host_ssh_key,
            allowed_disk_paths=settings.allowed_disk_paths,
            allowed_iso_paths=settings.allowed_iso_paths,
        )
        return [host], host.name

    local = HostConfig(
        name="local",
        uri="qemu:///system",
        allowed_disk_paths=settings.allowed_disk_paths,
        allowed_iso_paths=settings.allowed_iso_paths,
    )
    return [local], "local"


def resolve_hosts_file_path(settings: "Settings") -> str:
    """Return the hosts file path, defaulting to config/hosts.yaml if unset."""
    if settings.kvm_hosts_file:
        return settings.kvm_hosts_file
    return str(Path(__file__).resolve().parent.parent / "config" / "hosts.yaml")


def save_hosts_to_yaml(
    path: Path, hosts: list[HostConfig], default_host: str,
) -> None:
    """Persist the current host registry to a YAML file."""
    data = {
        "default_host": default_host,
        "hosts": [h.model_dump() for h in hosts],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    logger.info("Persisted %d host(s) to %s", len(hosts), path)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
