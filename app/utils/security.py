"""Enhanced security utilities for KVM MCP server.

Security validation concepts inspired by coolnyx/libvirt-mcp-server (MIT License).
Original project: https://github.com/coolnyx/libvirt-mcp-server
Enhanced for AGPL v3 license with additional security measures.
"""

import os
import pathlib
import re
from typing import List, Set

from app.config import HostConfig


class SecurityViolationError(Exception):
    """Raised when a security policy is violated."""
    
    def __init__(self, message: str, violation_type: str = "security_policy"):
        super().__init__(message)
        self.violation_type = violation_type


class PathSecurityValidator:
    """Enhanced path security validation with multiple security layers."""
    
    # Dangerous path patterns that should never be allowed
    DANGEROUS_PATTERNS = [
        r'\.\./',      # Path traversal
        r'/\.\./',     # Path traversal (absolute)
        r'^\.\.',      # Starting with parent directory
        r'/etc/',      # System configuration
        r'/proc/',     # Process information
        r'/sys/',      # System information
        r'/dev/',      # Device files
        r'/root/',     # Root user directory (unless explicitly allowed)
        r'/tmp/',      # Temporary files (potential security risk)
        r'[<>"|]',     # Command injection characters
        r';|`|\$\(',   # Shell injection patterns
    ]
    
    @classmethod
    def validate_disk_path(cls, disk_path: str, host_config: HostConfig) -> None:
        """Enhanced disk path validation with multiple security layers."""
        if not disk_path:
            raise SecurityViolationError("Disk path cannot be empty", "empty_path")
        
        # Basic path validation
        cls._validate_path_basic_security(disk_path)
        
        # Resolve path and check for traversal
        try:
            resolved = pathlib.Path(disk_path).resolve()
        except (OSError, ValueError) as e:
            raise SecurityViolationError(f"Invalid path: {e}", "invalid_path")
        
        # Check against allowed disk paths
        allowed_bases = [b.strip() for b in host_config.allowed_disk_paths.split(",") if b.strip()]
        if not allowed_bases:
            raise SecurityViolationError("No allowed disk paths configured", "no_allowed_paths")
        
        # Verify path is under allowed directories
        path_allowed = False
        for base in allowed_bases:
            try:
                base_resolved = pathlib.Path(base).resolve()
                if cls._is_path_under_directory(resolved, base_resolved):
                    path_allowed = True
                    break
            except (OSError, ValueError):
                continue  # Skip invalid base paths
        
        if not path_allowed:
            raise SecurityViolationError(
                f"Disk path '{disk_path}' not under allowed directories: {', '.join(allowed_bases)}",
                "path_not_allowed"
            )
        
        # Additional file extension validation
        cls._validate_disk_file_extension(disk_path)
    
    @classmethod
    def validate_iso_path(cls, iso_path: str, host_config: HostConfig) -> None:
        """Enhanced ISO path validation with multiple security layers."""
        if not iso_path:
            return  # ISO path is optional
        
        # Basic path validation
        cls._validate_path_basic_security(iso_path)
        
        # Resolve path and check for traversal
        try:
            resolved = pathlib.Path(iso_path).resolve()
        except (OSError, ValueError) as e:
            raise SecurityViolationError(f"Invalid ISO path: {e}", "invalid_path")
        
        # Check against allowed ISO paths
        allowed_bases = [b.strip() for b in host_config.allowed_iso_paths.split(",") if b.strip()]
        if not allowed_bases:
            raise SecurityViolationError("No allowed ISO paths configured", "no_allowed_paths")
        
        # Verify path is under allowed directories
        path_allowed = False
        for base in allowed_bases:
            try:
                base_resolved = pathlib.Path(base).resolve()
                if cls._is_path_under_directory(resolved, base_resolved):
                    path_allowed = True
                    break
            except (OSError, ValueError):
                continue  # Skip invalid base paths
        
        if not path_allowed:
            raise SecurityViolationError(
                f"ISO path '{iso_path}' not under allowed directories: {', '.join(allowed_bases)}",
                "path_not_allowed"
            )
        
        # Additional file extension validation
        cls._validate_iso_file_extension(iso_path)
    
    @classmethod
    def _validate_path_basic_security(cls, path: str) -> None:
        """Basic security validation for any file path."""
        if not path or not isinstance(path, str):
            raise SecurityViolationError("Path must be a non-empty string", "invalid_path")
        
        # Check for dangerous patterns
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                raise SecurityViolationError(
                    f"Path contains dangerous pattern: {path}",
                    "dangerous_pattern"
                )
        
        # Check for null bytes (directory traversal protection)
        if '\x00' in path:
            raise SecurityViolationError("Path contains null byte", "null_byte")
        
        # Check path length (prevent buffer overflow attacks)
        if len(path) > 4096:
            raise SecurityViolationError("Path too long (max 4096 characters)", "path_too_long")
        
        # Ensure path doesn't start with dangerous prefixes
        dangerous_starts = ['/dev/', '/proc/', '/sys/']
        for prefix in dangerous_starts:
            if path.lower().startswith(prefix):
                raise SecurityViolationError(f"Path starts with dangerous prefix: {prefix}", "dangerous_prefix")
    
    @classmethod
    def _is_path_under_directory(cls, path: pathlib.Path, directory: pathlib.Path) -> bool:
        """Check if path is under directory (handles symlinks safely)."""
        try:
            # Resolve both paths to handle symlinks
            path_resolved = path.resolve()
            dir_resolved = directory.resolve()
            
            # Check if path starts with directory
            return str(path_resolved).startswith(str(dir_resolved) + os.sep) or path_resolved == dir_resolved
        except (OSError, ValueError):
            return False
    
    @classmethod
    def _validate_disk_file_extension(cls, disk_path: str) -> None:
        """Validate disk file has allowed extension."""
        allowed_extensions = {'.qcow2', '.img', '.raw', '.vmdk', '.vdi', '.vhd'}
        path_obj = pathlib.Path(disk_path)
        
        if path_obj.suffix.lower() not in allowed_extensions:
            raise SecurityViolationError(
                f"Disk file must have allowed extension: {', '.join(sorted(allowed_extensions))}",
                "invalid_extension"
            )
    
    @classmethod
    def _validate_iso_file_extension(cls, iso_path: str) -> None:
        """Validate ISO file has allowed extension."""
        allowed_extensions = {'.iso', '.img'}
        path_obj = pathlib.Path(iso_path)
        
        if path_obj.suffix.lower() not in allowed_extensions:
            raise SecurityViolationError(
                f"ISO file must have allowed extension: {', '.join(sorted(allowed_extensions))}",
                "invalid_extension"
            )


def validate_vm_name_security(vm_name: str) -> None:
    """Enhanced VM name validation for security."""
    if not vm_name or not isinstance(vm_name, str):
        raise SecurityViolationError("VM name must be a non-empty string", "invalid_vm_name")
    
    if len(vm_name) > 64:
        raise SecurityViolationError("VM name too long (max 64 characters)", "vm_name_too_long")
    
    # Only allow alphanumeric, dots, underscores, hyphens
    if not re.match(r'^[a-zA-Z0-9._-]+$', vm_name):
        raise SecurityViolationError(
            "VM name can only contain alphanumeric characters, dots, underscores, and hyphens",
            "invalid_vm_name_chars"
        )
    
    # Prevent names that could cause issues
    forbidden_names = {'con', 'aux', 'nul', 'prn', 'com1', 'com2', 'com3', 'com4', 'lpt1', 'lpt2'}
    if vm_name.lower() in forbidden_names:
        raise SecurityViolationError(f"VM name '{vm_name}' is reserved", "reserved_vm_name")


def validate_snapshot_name_security(snapshot_name: str) -> None:
    """Enhanced snapshot name validation for security."""
    if not snapshot_name or not isinstance(snapshot_name, str):
        raise SecurityViolationError("Snapshot name must be a non-empty string", "invalid_snapshot_name")
    
    if len(snapshot_name) > 64:
        raise SecurityViolationError("Snapshot name too long (max 64 characters)", "snapshot_name_too_long")
    
    # Only allow alphanumeric, dots, underscores, hyphens
    if not re.match(r'^[a-zA-Z0-9._-]+$', snapshot_name):
        raise SecurityViolationError(
            "Snapshot name can only contain alphanumeric characters, dots, underscores, and hyphens",
            "invalid_snapshot_name_chars"
        )