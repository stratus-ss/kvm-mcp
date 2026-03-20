"""Role-Based Access Control middleware for FastMCP.

Based on RBAC concepts from coolnyx/libvirt-mcp-server (MIT License).
Original project: https://github.com/coolnyx/libvirt-mcp-server

Copyright notice for derived concepts:
Copyright (c) 2024 coolnyx
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files...

Adapted for FastMCP and AGPL v3 license.
"""

import asyncio
import fnmatch
import time
from collections import defaultdict
from functools import wraps
from typing import Any, Callable, Dict, List

from app.config import SecurityConfig


class RateLimiter:
    """Rate limiter to track operations per client per minute."""
    
    def __init__(self):
        self._client_counts: Dict[str, List[float]] = defaultdict(list)
    
    def is_allowed(self, client_id: str, max_per_minute: int) -> bool:
        """Check if client is within rate limits."""
        now = time.time()
        minute_ago = now - 60
        
        # Clean up old entries
        client_history = self._client_counts[client_id]
        self._client_counts[client_id] = [ts for ts in client_history if ts > minute_ago]
        
        # Check if under limit
        if len(self._client_counts[client_id]) >= max_per_minute:
            return False
            
        # Record this request
        self._client_counts[client_id].append(now)
        return True


class OperationFilter:
    """Operation filtering based on allowed patterns."""
    
    def __init__(self, allowed_operations: List[str]):
        self.allowed_operations = allowed_operations
    
    def is_allowed(self, operation_name: str) -> bool:
        """Check if operation matches allowed patterns."""
        # If '*' is in allowed operations, allow everything
        if "*" in self.allowed_operations:
            return True
            
        # Check if operation matches any allowed pattern
        for pattern in self.allowed_operations:
            if fnmatch.fnmatch(operation_name, pattern):
                return True
                
        return False


class ConcurrencyLimiter:
    """Limits concurrent operations globally."""
    
    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def acquire(self) -> bool:
        """Acquire a concurrency slot."""
        return await self.semaphore.acquire()
    
    def release(self):
        """Release a concurrency slot."""
        self.semaphore.release()


class RBACMiddleware:
    """RBAC middleware for FastMCP tool and resource handlers."""
    
    def __init__(self, security_config: SecurityConfig):
        self.security_config = security_config
        self.rate_limiter = RateLimiter()
        self.operation_filter = OperationFilter(security_config.allowed_operations)
        self.concurrency_limiter = ConcurrencyLimiter(security_config.max_concurrent_ops)
    
    def tool_decorator(self, tool_name: str):
        """Decorator for MCP tool handlers."""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                # Check operation is allowed
                if not self.operation_filter.is_allowed(tool_name):
                    raise PermissionError(f"Operation '{tool_name}' not allowed by RBAC policy")
                
                # Check rate limits (using tool name as client ID for now)
                # In a real implementation, you'd extract actual client ID from context
                client_id = f"tool_{tool_name}"
                if not self.rate_limiter.is_allowed(client_id, self.security_config.rate_limit_per_minute):
                    raise PermissionError(f"Rate limit exceeded for '{tool_name}' operations")
                
                # Acquire concurrency slot
                await self.concurrency_limiter.acquire()
                
                try:
                    # Execute the original function
                    result = await func(*args, **kwargs)
                    return result
                finally:
                    # Always release the concurrency slot
                    self.concurrency_limiter.release()
            
            return wrapper
        return decorator
    
    def resource_decorator(self, resource_name: str):
        """Decorator for MCP resource handlers."""  
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                # Check operation is allowed (resources use read permissions)
                read_operation = f"{resource_name}_read"
                if not self.operation_filter.is_allowed(read_operation):
                    raise PermissionError(f"Resource access '{resource_name}' not allowed by RBAC policy")
                
                # Execute the original function (no concurrency limiting for reads)
                result = await func(*args, **kwargs)
                return result
            
            return wrapper
        return decorator


def create_rbac_middleware(security_config: SecurityConfig) -> RBACMiddleware:
    """Factory function to create RBAC middleware instance."""
    return RBACMiddleware(security_config)


def rbac_tool(middleware: RBACMiddleware, tool_name: str):
    """Convenience decorator for applying RBAC to tool functions."""
    return middleware.tool_decorator(tool_name)


def rbac_resource(middleware: RBACMiddleware, resource_name: str):
    """Convenience decorator for applying RBAC to resource functions."""
    return middleware.resource_decorator(resource_name)