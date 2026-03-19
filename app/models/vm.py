"""VM data models."""

import pathlib
import re
from typing import Optional, List

from pydantic import BaseModel, Field, validator

from app.config import get_settings


def _validate_path_against_allowed(value: str, allowed_csv: str, field_name: str) -> str:
    """Resolve a path and check it falls under one of the allowed base directories."""
    resolved = pathlib.Path(value).resolve()
    if ".." in pathlib.Path(value).parts:
        raise ValueError(f"Path traversal (..) is not allowed in {field_name}")
    allowed_bases = [b.strip() for b in allowed_csv.split(",") if b.strip()]
    for base in allowed_bases:
        if str(resolved).startswith(str(pathlib.Path(base).resolve())):
            return str(resolved)
    raise ValueError(f"{field_name} must be under one of: {', '.join(allowed_bases)}")


def validate_vm_name(value: str) -> str:
    """
    Strict VM name validation.

    Args:
        value: VM name to validate

    Returns:
        Validated VM name

    Raises:
        ValueError: If VM name is invalid
    """
    if not value or not value.strip():
        raise ValueError("VM name cannot be empty")

    if not re.match(r'^[a-zA-Z0-9._-]+$', value):
        raise ValueError(
            f"Invalid VM name: '{value}'. "
            "Only alphanumeric characters, dots, underscores, and hyphens are allowed."
        )

    if len(value) > 64:
        raise ValueError(f"VM name too long: {len(value)} characters (max 64)")

    return value


def validate_snapshot_name(value: str) -> str:
    """
    Validate snapshot name.

    Args:
        value: Snapshot name to validate

    Returns:
        Validated snapshot name

    Raises:
        ValueError: If snapshot name is invalid
    """
    if not value or not value.strip():
        raise ValueError("Snapshot name cannot be empty")

    if not re.match(r'^[a-zA-Z0-9._-]+$', value):
        raise ValueError(
            f"Invalid snapshot name: '{value}'. "
            "Only alphanumeric characters, dots, underscores, and hyphens are allowed."
        )

    if len(value) > 64:
        raise ValueError(f"Snapshot name too long: {len(value)} characters (max 64)")

    return value


class VM(BaseModel):
    """VM model."""

    id: str = Field(..., description="VM ID")
    name: str = Field(..., description="VM name")
    status: str = Field(..., description="VM status (running, shut off, paused)")


class VMStatusResponse(BaseModel):
    """VM status response model."""

    vm: Optional[VM] = Field(None, description="VM information")
    running: bool = Field(..., description="Whether the VM is running")
    ip_address: Optional[str] = Field(None, description="IP address of the VM")


class BootOrderRequest(BaseModel):
    """Boot order request model."""

    boot_order: str = Field(
        ...,
        description="Boot order (e.g., 'hd,cdrom' or 'cdrom,hd')",
        examples=["hd,cdrom", "cdrom,hd"],
    )

    @validator('boot_order')
    def validate_boot_order(cls, v):
        """Validate boot order format."""
        valid_orders = ["hd,cdrom", "cdrom,hd", "network", "fd"]
        if v not in valid_orders:
            raise ValueError(
                f"Invalid boot order: '{v}'. Must be one of: {', '.join(valid_orders)}"
            )
        return v


class BootOrderResponse(BaseModel):
    """Boot order response model."""

    boot_order: str = Field(..., description="Current boot order")
    message: str = Field(..., description="Status message")


class VMActionResponse(BaseModel):
    """VM action response model."""

    success: bool = Field(..., description="Whether the action was successful")
    message: str = Field(..., description="Status message")


class IPAddressResponse(BaseModel):
    """IP address response model."""

    ip_address: Optional[str] = Field(None, description="IP address of the VM")
    message: str = Field(..., description="Status message")


class Snapshot(BaseModel):
    """Snapshot model."""

    name: str = Field(..., description="Snapshot name")
    time: str = Field(..., description="Snapshot creation time")


class SnapshotListResponse(BaseModel):
    """Snapshot list response model."""

    snapshots: list[Snapshot] = Field(..., description="List of snapshots")
    message: str = Field(..., description="Status message")


class SnapshotCreateRequest(BaseModel):
    """Snapshot create request model."""

    name: str = Field(..., description="Snapshot name", min_length=1, max_length=64)
    description: Optional[str] = Field(None, description="Snapshot description")

    @validator('name')
    def validate_name(cls, v):
        """Validate snapshot name."""
        return validate_snapshot_name(v)


class SnapshotRestoreRequest(BaseModel):
    """Snapshot restore request model."""

    snapshot_name: str = Field(..., description="Snapshot name to restore")

    @validator('snapshot_name')
    def validate_snapshot_name(cls, v):
        """Validate snapshot name."""
        return validate_snapshot_name(v)


class SnapshotRestoreResponse(BaseModel):
    """Snapshot restore response model."""

    success: bool = Field(..., description="Whether restore was successful")
    message: str = Field(..., description="Status message")


class VMCloneRequest(BaseModel):
    """VM clone request model."""

    target_name: str = Field(..., description="Name for cloned VM", min_length=1, max_length=64)
    memory_mb: Optional[int] = Field(None, description="Memory in MB (optional override)", ge=512, le=32768)
    vcpus: Optional[int] = Field(None, description="vCPUs (optional override)", ge=1, le=16)
    disk_size_gb: Optional[int] = Field(None, description="Disk size in GB (optional override)", ge=10, le=1000)

    @validator('target_name')
    def validate_target_name(cls, v):
        """Validate target VM name."""
        return validate_vm_name(v)


class VMCloneResponse(BaseModel):
    """VM clone response model."""

    success: bool = Field(..., description="Whether clone was successful")
    message: str = Field(..., description="Status message")
    vm: Optional[VM] = Field(None, description="Created VM information")


class DiskInfo(BaseModel):
    """Disk information model."""

    name: str = Field(..., description="Disk name")
    type: str = Field(..., description="Disk type")
    size: str = Field(..., description="Disk size")


class DiskListResponse(BaseModel):
    """Disk list response model."""

    disks: List[DiskInfo] = Field(..., description="List of attached disks")
    message: str = Field(..., description="Status message")


class DiskAttachRequest(BaseModel):
    """Disk attach request model."""

    disk_path: str = Field(..., description="Path to disk file or block device")
    mode: str = Field(default="rw", description="Disk mode (ro/rw)")

    @validator('disk_path')
    def validate_disk_path(cls, v):
        """Validate disk_path is under an allowed base directory."""
        settings = get_settings()
        return _validate_path_against_allowed(v, settings.allowed_disk_paths, "disk_path")


class DiskResizeRequest(BaseModel):
    """Disk resize request model."""

    new_size_gb: int = Field(..., description="New disk size in GB", ge=10, le=1000)


class NetworkInfo(BaseModel):
    """Network information model."""

    name: str = Field(..., description="Network name")
    state: str = Field(..., description="Network state")


class NetworkListResponse(BaseModel):
    """Network list response model."""

    networks: List[NetworkInfo] = Field(..., description="List of available networks")
    message: str = Field(..., description="Status message")


class NetworkAttachRequest(BaseModel):
    """Network attach request model."""

    network_name: str = Field(..., description="Network name")


class VMCreateRequest(BaseModel):
    """VM create request model."""

    name: str = Field(..., description="VM name", min_length=1, max_length=64)
    memory_mb: int = Field(default=2048, description="Memory in MB", ge=512, le=32768)
    vcpus: int = Field(default=2, description="Number of vCPUs", ge=1, le=16)
    disk_size_gb: int = Field(default=20, description="Disk size in GB", ge=10, le=1000)
    disk_path: str = Field(default="/var/lib/libvirt/images", description="Disk storage path")
    iso_path: Optional[str] = Field(None, description="Path to ISO image for installation")
    os_variant: Optional[str] = Field(None, description="OS variant (e.g., ubuntu24.04, win11, etc.)")

    @validator('name')
    def validate_name(cls, v):
        """Validate VM name."""
        return validate_vm_name(v)

    @validator('disk_path')
    def validate_disk_path(cls, v):
        """Validate disk_path is under an allowed base directory."""
        settings = get_settings()
        return _validate_path_against_allowed(v, settings.allowed_disk_paths, "disk_path")

    @validator('iso_path')
    def validate_iso_path(cls, v):
        """Validate iso_path is under an allowed base directory."""
        if v is None:
            return v
        settings = get_settings()
        return _validate_path_against_allowed(v, settings.allowed_iso_paths, "iso_path")

    @validator('os_variant')
    def validate_os_variant(cls, v):
        """Validate OS variant format."""
        if v is not None and not re.match(r'^[a-zA-Z0-9\.\-]*$', v):
            raise ValueError(f"Invalid OS variant: '{v}'")
        return v
