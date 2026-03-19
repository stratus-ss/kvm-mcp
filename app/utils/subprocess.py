"""Subprocess execution utilities."""

import asyncio
import shlex
from typing import Optional

from app.config import get_settings

settings = get_settings()


async def run_command(
    command: str | list[str],
    host: Optional[str] = None,
    user: Optional[str] = None,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """
    Run a command on local or remote host using list-based construction.

    Args:
        command: Command to execute (string or list)
        host: Remote host (None for local execution)
        user: Remote user (None for local execution)
        timeout: Timeout in seconds

    Returns:
        Tuple of (return_code, stdout, stderr)

    Raises:
        asyncio.TimeoutError: If command execution exceeds timeout
    """
    # Convert command to list for safe execution
    if isinstance(command, str):
        if host and user:
            # Remote execution: build SSH command as list
            full_command = [
                "ssh",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10",
                f"{user}@{host}",
                command
            ]
        else:
            # Local execution: parse string into arguments
            import shlex
            full_command = shlex.split(command)
    else:
        # Command is already a list
        full_command = command

    # Use create_subprocess_exec instead of create_subprocess_shell to prevent injection
    # Unpack the list using *full_command to avoid passing list as program
    process = await asyncio.create_subprocess_exec(
        *full_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise


def run_command_sync(
    command: str,
    host: Optional[str] = None,
    user: Optional[str] = None,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """
    Run a command synchronously on local or remote host.

    Args:
        command: Command to execute
        host: Remote host (None for local execution)
        user: Remote user (None for local execution)
        timeout: Timeout in seconds

    Returns:
        Tuple of (return_code, stdout, stderr)

    Raises:
        subprocess.TimeoutExpired: If command execution exceeds timeout
    """
    import subprocess
    import shlex

    if host and user:
        # Execute on remote host via SSH
        full_command = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            f"{user}@{host}",
            command,
        ]
    else:
        # Execute locally - check if command is already a list or needs parsing
        if isinstance(command, list):
            full_command = command
        else:
            # Parse command string into arguments
            full_command = shlex.split(command)

    result = subprocess.run(
        full_command,
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    return (
        result.returncode,
        result.stdout,
        result.stderr,
    )
