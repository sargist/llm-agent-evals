"""Helpers to start the bank MCP server as a subprocess and talk to it over stdio."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
from pydantic import AnyUrl

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_URI = AnyUrl("bank://state")


def server_params(
    customer_id: str, scenarios: list[str] | tuple[str, ...] = (), test_hooks: bool = True
) -> StdioServerParameters:
    env = {
        "ACME_CUSTOMER_ID": customer_id,
        "ACME_SCENARIO": ",".join(scenarios),
        "ACME_TEST_HOOKS": "1" if test_hooks else "0",
        "PYTHONPATH": str(REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PYTHONIOENCODING": "utf-8",
    }
    return StdioServerParameters(
        command=sys.executable, args=["-m", "bank_agent.mcp_server"], env=env, cwd=str(REPO_ROOT)
    )


@asynccontextmanager
async def bank_session(
    customer_id: str, scenarios: list[str] | tuple[str, ...] = (), test_hooks: bool = True
) -> AsyncIterator[ClientSession]:
    """A fresh server process (fresh bank state) and an initialized MCP client session."""
    with open(os.devnull, "w") as errlog:
        async with (
            stdio_client(server_params(customer_id, scenarios, test_hooks), errlog=errlog) as (
                read,
                write,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


def result_payload(result: CallToolResult) -> Any:
    """The tool result as plain JSON types (structured content if present, else the text)."""
    if result.structuredContent is not None and not result.isError:
        return result.structuredContent
    text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
    try:
        return json.loads(text)
    except ValueError:
        return text


async def read_state(session: ClientSession) -> dict:
    result = await session.read_resource(STATE_URI)
    return json.loads(result.contents[0].text)
