"""Structured audit logging for MCP server operations."""

import functools
import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Callable

_AUDIT_LOG_DIR = os.environ.get("MCP_AUDIT_LOG_DIR", "/var/log/kvm-mcp")
_AUDIT_LOG_FILE = os.path.join(_AUDIT_LOG_DIR, "mcp-audit.jsonl")
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
_BACKUP_COUNT = 5


class AuditLogger:
    """Structured JSON-lines audit logger for MCP tool calls and resource reads."""

    def __init__(self, log_dir: str = _AUDIT_LOG_DIR):
        self._logger = logging.getLogger("mcp.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        if self._logger.handlers:
            return

        log_file = os.path.join(log_dir, "mcp-audit.jsonl")

        try:
            os.makedirs(log_dir, exist_ok=True)
            handler = RotatingFileHandler(
                log_file,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
            )
        except OSError:
            handler = logging.StreamHandler()

        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)

    def _emit(self, record: dict[str, Any]) -> None:
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._logger.info(json.dumps(record, default=str))

    def tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        result: str | None = None,
        error: str | None = None,
        duration_ms: float = 0,
    ) -> None:
        self._emit({
            "event": "tool_call",
            "tool": tool,
            "args": _sanitise_args(args),
            "ok": error is None,
            "error": error,
            "duration_ms": round(duration_ms, 1),
            "result_length": len(result) if result else 0,
        })

    def resource_read(
        self,
        uri: str,
        error: str | None = None,
        duration_ms: float = 0,
    ) -> None:
        self._emit({
            "event": "resource_read",
            "uri": uri,
            "ok": error is None,
            "error": error,
            "duration_ms": round(duration_ms, 1),
        })


def _sanitise_args(args: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive argument values."""
    sensitive_keys = {"public_key", "ssh_key", "password", "secret"}
    return {
        k: "***REDACTED***" if k in sensitive_keys else v
        for k, v in args.items()
    }


_audit = AuditLogger()


def audited_tool(fn: Callable) -> Callable:
    """Decorator that wraps an async MCP tool handler with audit logging."""

    @functools.wraps(fn)
    async def wrapper(**kwargs: Any) -> str:
        start = time.monotonic()
        try:
            result = await fn(**kwargs)
            elapsed = (time.monotonic() - start) * 1000
            _audit.tool_call(
                tool=fn.__name__,
                args=kwargs,
                result=result,
                duration_ms=elapsed,
            )
            return result
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            _audit.tool_call(
                tool=fn.__name__,
                args=kwargs,
                error=str(exc),
                duration_ms=elapsed,
            )
            raise

    return wrapper


def audited_resource(uri_pattern: str) -> Callable:
    """Decorator factory that wraps an async MCP resource handler with audit logging."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            uri = uri_pattern
            for key, val in kwargs.items():
                uri = uri.replace(f"{{{key}}}", str(val))
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
                elapsed = (time.monotonic() - start) * 1000
                _audit.resource_read(uri=uri, duration_ms=elapsed)
                return result
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                _audit.resource_read(uri=uri, error=str(exc), duration_ms=elapsed)
                raise

        return wrapper
    return decorator
