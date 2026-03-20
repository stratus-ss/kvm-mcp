"""Role-Based Access Control service for KVM MCP server."""

import os
import json
import re
import time
from typing import Dict, List, Optional, Set
from pathlib import Path

from app.config import get_settings
from app.models.rbac import (
    User, Role, Permission, RoleDefinition, AccessPolicy, RBACContext,
    DEFAULT_ROLE_DEFINITIONS
)


class RBACService:
    """Service for managing role-based access control."""

    def __init__(self):
        self.users: Dict[str, User] = {}
        self.roles: Dict[Role, RoleDefinition] = {rd.role: rd for rd in DEFAULT_ROLE_DEFINITIONS}
        self.policy = AccessPolicy()
        self.user_sessions: Dict[str, Dict] = {}  # Track user sessions and rate limits
        self._load_configuration()

    def _load_configuration(self) -> None:
        """Load RBAC configuration from environment and files."""
        settings = get_settings()
        
        # Load policy from environment
        self.policy.enabled = os.environ.get("SECURITY_AUTH_REQUIRED", "false").lower() == "true"
        self.policy.require_authentication = self.policy.enabled
        self.policy.max_concurrent_operations = int(os.environ.get("SECURITY_MAX_CONCURRENT_OPS", "20"))
        self.policy.rate_limit_per_minute = int(os.environ.get("SECURITY_RATE_LIMIT_PER_MINUTE", "300"))
        
        # Load users from file if it exists
        users_file = os.environ.get("RBAC_USERS_FILE", "rbac_users.json")
        if Path(users_file).exists():
            self._load_users_from_file(users_file)

    def _load_users_from_file(self, file_path: str) -> None:
        """Load users from JSON file."""
        try:
            with open(file_path) as f:
                users_data = json.load(f)
            
            for user_data in users_data.get("users", []):
                user = User(**user_data)
                self.users[user.id] = user
        except Exception as e:
            print(f"Warning: Could not load users file {file_path}: {e}")

    def get_user_permissions(self, user_id: str) -> Set[Permission]:
        """Get all permissions for a user based on roles and custom permissions."""
        user = self.users.get(user_id)
        if not user or not user.enabled:
            return set()

        permissions = set(user.custom_permissions)
        
        # Add permissions from roles
        for role in user.roles:
            role_def = self.roles.get(role)
            if role_def:
                permissions.update(role_def.permissions)

        # Admin role gets all permissions
        if Permission.ADMIN_ALL in permissions:
            return set(Permission)

        return permissions

    def check_permission(self, context: RBACContext) -> tuple[bool, str]:
        """Check if user has permission for the given context."""
        if not self.policy.enabled:
            return True, "RBAC disabled"

        user = self.users.get(context.user_id)
        if not user:
            return False, f"User '{context.user_id}' not found"

        if not user.enabled:
            return False, f"User '{context.user_id}' is disabled"

        # Check rate limiting
        if not self._check_rate_limit(context.user_id):
            return False, "Rate limit exceeded"

        # Get required permission for the operation
        required_permission = self._map_operation_to_permission(context.operation)
        if not required_permission:
            return False, f"Unknown operation: {context.operation}"

        # Get user permissions
        user_permissions = self.get_user_permissions(context.user_id)
        
        # Check if user has the required permission
        if required_permission not in user_permissions:
            return False, f"Missing permission: {required_permission.value}"

        # Check host restrictions
        if user.host_restrictions and context.host:
            if context.host not in user.host_restrictions:
                return False, f"Access to host '{context.host}' not allowed"

        # Check VM name pattern restrictions
        if user.vm_name_patterns and context.resource_name and context.resource_type == "vm":
            if not self._check_vm_name_patterns(context.resource_name, user.vm_name_patterns):
                return False, f"VM name '{context.resource_name}' not allowed by patterns"

        return True, "Access granted"

    def _map_operation_to_permission(self, operation: str) -> Optional[Permission]:
        """Map MCP operation to required permission."""
        operation_map = {
            # VM operations
            "kvm_list_vms": Permission.VM_LIST,
            "kvm_get_vm_status": Permission.VM_STATUS,
            "kvm_start_vm": Permission.VM_START,
            "kvm_stop_vm": Permission.VM_STOP,
            "kvm_restart_vm": Permission.VM_RESTART,
            "kvm_create_vm": Permission.VM_CREATE,
            "kvm_delete_vm": Permission.VM_DELETE,
            "kvm_clone_vm": Permission.VM_CLONE,
            "ensure_vm_running": Permission.VM_START,
            "ensure_vm_stopped": Permission.VM_STOP,
            "ensure_vm_exists": Permission.VM_CREATE,
            
            # Boot order
            "kvm_get_boot_order": Permission.BOOT_GET,
            "kvm_set_boot_order": Permission.BOOT_SET,
            
            # Snapshots
            "kvm_list_snapshots": Permission.SNAPSHOT_LIST,
            "kvm_create_snapshot": Permission.SNAPSHOT_CREATE,
            "kvm_delete_snapshot": Permission.SNAPSHOT_DELETE,
            "kvm_restore_snapshot": Permission.SNAPSHOT_RESTORE,
            "ensure_snapshot_exists": Permission.SNAPSHOT_CREATE,
            
            # Disks
            "kvm_list_disks": Permission.DISK_LIST,
            "kvm_attach_disk": Permission.DISK_ATTACH,
            "kvm_detach_disk": Permission.DISK_DETACH,
            "kvm_resize_disk": Permission.DISK_RESIZE,
            "ensure_disk_attached": Permission.DISK_ATTACH,
            
            # Networks
            "kvm_list_networks": Permission.NETWORK_LIST,
            "kvm_attach_network": Permission.NETWORK_ATTACH,
            "kvm_detach_network": Permission.NETWORK_DETACH,
            
            # Guest operations
            "guest_ping": Permission.GUEST_PING,
            "guest_exec": Permission.GUEST_EXEC,
            "guest_get_network": Permission.GUEST_NETWORK,
            "guest_get_ip": Permission.GUEST_IP,
            "guest_inject_ssh_key": Permission.GUEST_SSH_KEY,
            
            # Fleet operations
            "kvm_list_hosts": Permission.FLEET_LIST,
            "kvm_fleet_status": Permission.FLEET_STATUS,
            
            # Resources
            "resource_list_hosts": Permission.RESOURCE_READ,
            "resource_list_vms": Permission.RESOURCE_READ,
            "resource_list_networks": Permission.RESOURCE_READ,
            "resource_vm_detail": Permission.RESOURCE_READ,
            "resource_vm_snapshots": Permission.RESOURCE_READ,
            "resource_vm_disks": Permission.RESOURCE_READ,
            "resource_host_vms": Permission.RESOURCE_READ,
            "resource_storage_pools": Permission.RESOURCE_READ,
        }
        
        return operation_map.get(operation)

    def _check_rate_limit(self, user_id: str) -> bool:
        """Check if user is within rate limits."""
        if not self.policy.rate_limit_per_minute:
            return True

        now = time.time()
        session = self.user_sessions.setdefault(user_id, {"operations": []})
        
        # Clean old operations
        session["operations"] = [
            op_time for op_time in session["operations"]
            if now - op_time < 60  # Keep last minute
        ]
        
        # Check rate limit
        if len(session["operations"]) >= self.policy.rate_limit_per_minute:
            return False
        
        # Record this operation
        session["operations"].append(now)
        return True

    def _check_vm_name_patterns(self, vm_name: str, patterns: List[str]) -> bool:
        """Check if VM name matches allowed patterns."""
        if not patterns:
            return True
        
        for pattern in patterns:
            try:
                if re.match(pattern, vm_name):
                    return True
            except re.error:
                # Treat invalid regex as literal string match
                if pattern == vm_name:
                    return True
        
        return False

    def add_user(self, user: User) -> None:
        """Add or update a user."""
        self.users[user.id] = user

    def remove_user(self, user_id: str) -> bool:
        """Remove a user."""
        return self.users.pop(user_id, None) is not None

    def get_user(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        return self.users.get(user_id)

    def list_users(self) -> List[User]:
        """List all users."""
        return list(self.users.values())

    def get_role_permissions(self, role: Role) -> Set[Permission]:
        """Get permissions for a role."""
        role_def = self.roles.get(role)
        if not role_def:
            return set()
        
        if Permission.ADMIN_ALL in role_def.permissions:
            return set(Permission)
        
        return set(role_def.permissions)

    def save_users_to_file(self, file_path: str) -> None:
        """Save users to JSON file."""
        users_data = {
            "users": [user.model_dump() for user in self.users.values()]
        }
        
        with open(file_path, 'w') as f:
            json.dump(users_data, f, indent=2)

    def is_enabled(self) -> bool:
        """Check if RBAC is enabled."""
        return self.policy.enabled