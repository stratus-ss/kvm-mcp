"""RBAC authentication utilities for MCP tools."""

import functools
from typing import Any, Callable, Optional

from app.services.rbac_service import RBACService
from app.models.rbac import RBACContext


# Global RBAC service instance
_rbac_service: Optional[RBACService] = None


def get_rbac_service() -> RBACService:
    """Get or create the RBAC service instance."""
    global _rbac_service
    if _rbac_service is None:
        _rbac_service = RBACService()
    return _rbac_service


def extract_user_id(context: dict) -> str:
    """Extract user ID from MCP context."""
    # In a real implementation, this would extract from:
    # - HTTP headers (Authorization bearer token)
    # - SSL certificate subject
    # - Environment variables
    # - Session data
    
    # For now, use environment variable or default
    import os
    return os.environ.get("MCP_USER_ID", "anonymous")


def rbac_protected(operation_name: Optional[str] = None):
    """Decorator to protect MCP tools with RBAC."""
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            rbac = get_rbac_service()
            
            # Skip RBAC if disabled
            if not rbac.is_enabled():
                return await func(*args, **kwargs)
            
            # Extract context information
            user_id = extract_user_id({})  # In real implementation, pass MCP context
            op_name = operation_name or func.__name__
            
            # Determine resource information from arguments
            resource_type = "vm"
            resource_name = None
            host = kwargs.get("host", "") or ""
            
            # Extract VM name from common argument patterns
            for arg_name in ["vm_name", "name", "source_vm_name", "target_name"]:
                if arg_name in kwargs and kwargs[arg_name]:
                    resource_name = kwargs[arg_name]
                    break
            
            # Create RBAC context
            context = RBACContext(
                user_id=user_id,
                operation=op_name,
                resource_type=resource_type,
                resource_name=resource_name,
                host=host,
                additional_params={k: v for k, v in kwargs.items() if k not in ["host"]}
            )
            
            # Check permission
            allowed, reason = rbac.check_permission(context)
            if not allowed:
                return f"❌ Access denied: {reason}"
            
            # Execute the original function
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


def require_permission(permission_name: str):
    """Decorator to require a specific permission."""
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            rbac = get_rbac_service()
            
            if not rbac.is_enabled():
                return await func(*args, **kwargs)
            
            user_id = extract_user_id({})
            user_permissions = rbac.get_user_permissions(user_id)
            
            # Check if user has the required permission
            from app.models.rbac import Permission
            required_perm = Permission(permission_name)
            
            if required_perm not in user_permissions:
                return f"❌ Access denied: Missing permission {permission_name}"
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


# Convenience decorators for common permission levels
def admin_only(func: Callable) -> Callable:
    """Decorator for admin-only operations."""
    return require_permission("admin:*")(func)


def operator_or_higher(func: Callable) -> Callable:
    """Decorator requiring operator permissions or higher."""
    return rbac_protected()(func)


def viewer_or_higher(func: Callable) -> Callable:
    """Decorator requiring viewer permissions or higher.""" 
    return rbac_protected()(func)