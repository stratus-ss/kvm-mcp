"""RBAC models for KVM MCP server."""

from enum import Enum
from typing import List, Optional, Set, Dict, Any
from pydantic import BaseModel, Field


class Permission(str, Enum):
    """Available permissions in the system."""
    
    # VM Lifecycle
    VM_LIST = "vm:list"
    VM_CREATE = "vm:create"
    VM_DELETE = "vm:delete"
    VM_START = "vm:start"
    VM_STOP = "vm:stop"
    VM_RESTART = "vm:restart"
    VM_CLONE = "vm:clone"
    VM_STATUS = "vm:status"
    VM_INFO = "vm:info"
    
    # Boot Order
    BOOT_GET = "boot:get"
    BOOT_SET = "boot:set"
    
    # Snapshots
    SNAPSHOT_LIST = "snapshot:list"
    SNAPSHOT_CREATE = "snapshot:create"
    SNAPSHOT_DELETE = "snapshot:delete"
    SNAPSHOT_RESTORE = "snapshot:restore"
    
    # Disks
    DISK_LIST = "disk:list"
    DISK_ATTACH = "disk:attach"
    DISK_DETACH = "disk:detach"
    DISK_RESIZE = "disk:resize"
    DISK_CREATE = "disk:create"
    
    # Networks
    NETWORK_LIST = "network:list"
    NETWORK_ATTACH = "network:attach"
    NETWORK_DETACH = "network:detach"
    
    # Guest Agent
    GUEST_PING = "guest:ping"
    GUEST_EXEC = "guest:exec"
    GUEST_NETWORK = "guest:network"
    GUEST_IP = "guest:ip"
    GUEST_SSH_KEY = "guest:ssh_key"
    
    # Fleet Management
    FLEET_LIST = "fleet:list"
    FLEET_STATUS = "fleet:status"
    
    # Resources (read-only)
    RESOURCE_READ = "resource:read"
    
    # Admin operations
    ADMIN_ALL = "admin:*"


class Role(str, Enum):
    """Predefined roles with different access levels."""
    
    ADMIN = "admin"
    OPERATOR = "operator"
    DEVELOPER = "developer"
    VIEWER = "viewer"
    GUEST = "guest"


class User(BaseModel):
    """User model with role and permissions."""
    
    id: str = Field(..., description="Unique user identifier")
    name: str = Field(..., description="Display name")
    email: Optional[str] = Field(None, description="Email address")
    roles: List[Role] = Field(default=[], description="Assigned roles")
    custom_permissions: List[Permission] = Field(default=[], description="Additional permissions")
    host_restrictions: List[str] = Field(default=[], description="Allowed hosts (empty = all)")
    vm_name_patterns: List[str] = Field(default=[], description="Allowed VM name patterns")
    enabled: bool = Field(default=True, description="Whether user is active")


class RoleDefinition(BaseModel):
    """Role definition with permissions."""
    
    role: Role = Field(..., description="Role identifier")
    name: str = Field(..., description="Display name")
    description: str = Field(..., description="Role description")
    permissions: List[Permission] = Field(..., description="Granted permissions")


class AccessPolicy(BaseModel):
    """Access policy configuration."""
    
    enabled: bool = Field(default=True, description="Whether RBAC is enabled")
    default_role: Role = Field(default=Role.GUEST, description="Default role for new users")
    require_authentication: bool = Field(default=False, description="Require authentication")
    max_concurrent_operations: int = Field(default=20, description="Max operations per user")
    rate_limit_per_minute: int = Field(default=300, description="Operations per minute limit")


class RBACContext(BaseModel):
    """Context for RBAC evaluation."""
    
    user_id: str = Field(..., description="User identifier")
    operation: str = Field(..., description="Operation being performed")
    resource_type: str = Field(..., description="Resource type (vm, host, etc.)")
    resource_name: Optional[str] = Field(None, description="Resource name")
    host: Optional[str] = Field(None, description="Target host")
    additional_params: Dict[str, Any] = Field(default={}, description="Additional parameters")


# Predefined role definitions
DEFAULT_ROLE_DEFINITIONS = [
    RoleDefinition(
        role=Role.ADMIN,
        name="Administrator",
        description="Full access to all operations across all hosts",
        permissions=[Permission.ADMIN_ALL]
    ),
    RoleDefinition(
        role=Role.OPERATOR,
        name="Operator",
        description="VM lifecycle management and monitoring",
        permissions=[
            Permission.VM_LIST, Permission.VM_CREATE, Permission.VM_DELETE,
            Permission.VM_START, Permission.VM_STOP, Permission.VM_RESTART,
            Permission.VM_CLONE, Permission.VM_STATUS, Permission.VM_INFO,
            Permission.SNAPSHOT_LIST, Permission.SNAPSHOT_CREATE, Permission.SNAPSHOT_DELETE,
            Permission.SNAPSHOT_RESTORE, Permission.DISK_LIST, Permission.DISK_ATTACH,
            Permission.DISK_DETACH, Permission.DISK_RESIZE, Permission.DISK_CREATE,
            Permission.NETWORK_LIST, Permission.NETWORK_ATTACH, Permission.NETWORK_DETACH,
            Permission.BOOT_GET, Permission.BOOT_SET,
            Permission.GUEST_PING, Permission.GUEST_NETWORK, Permission.GUEST_IP,
            Permission.GUEST_SSH_KEY, Permission.FLEET_LIST, Permission.FLEET_STATUS,
            Permission.RESOURCE_READ
        ]
    ),
    RoleDefinition(
        role=Role.DEVELOPER,
        name="Developer",
        description="Development and testing operations with limited guest access",
        permissions=[
            Permission.VM_LIST, Permission.VM_CREATE, Permission.VM_DELETE,
            Permission.VM_START, Permission.VM_STOP, Permission.VM_RESTART,
            Permission.VM_CLONE, Permission.VM_STATUS, Permission.VM_INFO,
            Permission.SNAPSHOT_LIST, Permission.SNAPSHOT_CREATE, Permission.SNAPSHOT_DELETE,
            Permission.SNAPSHOT_RESTORE, Permission.DISK_LIST, Permission.DISK_ATTACH,
            Permission.DISK_DETACH, Permission.NETWORK_LIST, Permission.NETWORK_ATTACH,
            Permission.NETWORK_DETACH, Permission.BOOT_GET, Permission.BOOT_SET,
            Permission.GUEST_PING, Permission.GUEST_EXEC, Permission.GUEST_NETWORK,
            Permission.GUEST_IP, Permission.RESOURCE_READ
        ]
    ),
    RoleDefinition(
        role=Role.VIEWER,
        name="Viewer",
        description="Read-only access for monitoring and reporting",
        permissions=[
            Permission.VM_LIST, Permission.VM_STATUS, Permission.VM_INFO,
            Permission.SNAPSHOT_LIST, Permission.DISK_LIST, Permission.NETWORK_LIST,
            Permission.BOOT_GET, Permission.GUEST_PING, Permission.GUEST_NETWORK,
            Permission.GUEST_IP, Permission.FLEET_LIST, Permission.FLEET_STATUS,
            Permission.RESOURCE_READ
        ]
    ),
    RoleDefinition(
        role=Role.GUEST,
        name="Guest",
        description="Minimal read access for basic monitoring",
        permissions=[
            Permission.VM_LIST, Permission.VM_STATUS, Permission.FLEET_STATUS,
            Permission.RESOURCE_READ
        ]
    )
]