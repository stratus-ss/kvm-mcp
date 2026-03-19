"""Configuration management for KVM MCP server."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class HostConfig(BaseModel):
    """Configuration for a single KVM host."""

    name: str
    uri: str = "qemu:///system"
    ssh_user: str = "root"
    ssh_key: str = "~/.ssh/id_rsa"
    allowed_disk_paths: str = "/var/lib/libvirt/images"
    allowed_iso_paths: str = "/var/lib/libvirt/images,/home"


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


def load_host_configs(settings: Settings) -> tuple[list[HostConfig], str]:
    """Resolve host configurations. Returns (hosts, default_host_name)."""
    hosts_file = settings.kvm_hosts_file
    if hosts_file and Path(hosts_file).is_file():
        import yaml

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


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
