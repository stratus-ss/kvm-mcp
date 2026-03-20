"""HTTP transport configuration for KVM MCP server."""

import os
from typing import Dict, Any, Optional
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


class HTTPTransportConfig:
    """Configuration for HTTP transport."""

    def __init__(self):
        self.host = os.environ.get("MCP_HOST", "0.0.0.0")
        self.port = int(os.environ.get("MCP_PORT", "8000"))
        self.enable_auth = os.environ.get("MCP_ENABLE_AUTH", "false").lower() == "true"
        self.auth_token = os.environ.get("MCP_AUTH_TOKEN")
        self.cors_origins = os.environ.get("MCP_CORS_ORIGINS", "*").split(",")
        self.enable_metrics = os.environ.get("MCP_ENABLE_METRICS", "false").lower() == "true"


class HTTPTransportSecurity:
    """Security middleware for HTTP transport."""

    def __init__(self, config: HTTPTransportConfig):
        self.config = config
        self.bearer_scheme = HTTPBearer(auto_error=False)

    async def verify_token(
        self, credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
    ):
        """Verify authentication token for HTTP requests."""
        if not self.config.enable_auth:
            return True

        if not credentials or not self.config.auth_token:
            raise HTTPException(status_code=401, detail="Authentication required")

        if credentials.credentials != self.config.auth_token:
            raise HTTPException(status_code=403, detail="Invalid authentication token")

        return True


def get_transport_config() -> Dict[str, Any]:
    """Get transport configuration for different protocols."""
    config = HTTPTransportConfig()
    
    transport_type = os.environ.get("MCP_TRANSPORT", "stdio")
    
    if transport_type == "http":
        return {
            "transport": "http",
            "host": config.host,
            "port": config.port,
            "enable_cors": True,
            "cors_origins": config.cors_origins,
        }
    elif transport_type == "streamable-http":
        return {
            "transport": "streamable-http",
            "host": config.host,
            "port": config.port,
            "enable_cors": True,
            "cors_origins": config.cors_origins,
        }
    else:
        return {"transport": "stdio"}


def create_http_middleware() -> Dict[str, Any]:
    """Create HTTP middleware configuration."""
    config = HTTPTransportConfig()
    security = HTTPTransportSecurity(config)
    
    middleware = {}
    
    if config.enable_auth:
        middleware["security"] = security
    
    if config.enable_metrics:
        middleware["metrics"] = True
    
    return middleware