#!/usr/bin/env python3
"""Bridge between bash test script and the MCP server.

Reads line-based commands from stdin, dispatches them to the FastMCP
instance, and writes single-line JSON results to stdout.

Protocol
--------
Commands (stdin, one per line):
    TOOLS                               → list tool names
    RESOURCES                           → list static resource URIs
    TEMPLATES                           → list resource template URIs
    PROMPTS                             → list prompt names
    CALL <tool_name> [<json_args>]      → invoke a tool
    READ <uri>                          → read a resource
    PROMPT <name> [<json_args>]         → render a prompt
    QUIT                                → shut down

Responses (stdout, one JSON object per line):
    {"tools": [...]}
    {"text": "..."}
    {"error": "..."}
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp.exceptions import ToolError

from app.mcp_server import mcp


async def _handle(line: str) -> dict:
    parts = line.split(None, 2)
    cmd = parts[0].upper()

    if cmd == "TOOLS":
        tools = await mcp.list_tools()
        return {"tools": sorted(t.name for t in tools)}

    if cmd == "RESOURCES":
        resources = await mcp.list_resources()
        return {"resources": sorted(str(r.uri) for r in resources)}

    if cmd == "TEMPLATES":
        templates = await mcp.list_resource_templates()
        return {"templates": sorted(t.uriTemplate for t in templates)}

    if cmd == "PROMPTS":
        prompts = await mcp.list_prompts()
        return {"prompts": sorted(p.name for p in prompts)}

    if cmd == "CALL":
        name = parts[1]
        args = json.loads(parts[2]) if len(parts) > 2 else {}
        result = await mcp.call_tool(name, args)
        return {"text": result[0][0].text}

    if cmd == "READ":
        uri = parts[1]
        result = await mcp.read_resource(uri)
        return {"text": result[0].content}

    if cmd == "PROMPT":
        name = parts[1]
        args = json.loads(parts[2]) if len(parts) > 2 else {}
        result = await mcp.get_prompt(name, arguments=args)
        return {"text": result.messages[0].content.text}

    return {"error": f"unknown command: {cmd}"}


async def main() -> None:
    print("READY", flush=True)
    loop = asyncio.get_event_loop()

    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line or line == "QUIT":
            break
        try:
            result = await _handle(line)
        except ToolError as exc:
            result = {"error": f"ToolError: {exc}"}
        except Exception as exc:
            result = {"error": str(exc)}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
