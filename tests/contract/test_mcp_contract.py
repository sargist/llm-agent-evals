"""Contract tests for the Acme Bank MCP server, through a real MCP client over stdio.

Every test starts its own server process, so state never leaks between tests.

Covered: tool discovery, input schemas, output contracts (JSON Schema), input validation,
business-rule errors, authorization boundaries, state changes, session isolation and
test-hook gating.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import jsonschema
import pytest
from mcp.types import CallToolResult, TextContent

from bank_agent.mcp_client import REPO_ROOT, bank_session, read_state, result_payload

MONEY = {"type": "string", "pattern": r"^-?\d+\.\d{2}$"}
ACCOUNT_ID = {"type": "string", "pattern": r"^ACC-\d{4}$"}
CARD_ID = {"type": "string", "pattern": r"^CARD-\d{4}$"}
CURRENCY = {"type": "string", "pattern": r"^[A-Z]{3}$"}


def obj(required: dict, optional: dict | None = None) -> dict:
    return {
        "type": "object",
        "required": sorted(required),
        "additionalProperties": False,
        "properties": {**required, **(optional or {})},
    }


# The consumer's view of each tool's output. Stricter than the server's generated outputSchema.
OUTPUT_CONTRACTS = {
    "get_accounts": obj(
        {
            "accounts": {
                "type": "array",
                "minItems": 1,
                "items": obj(
                    {
                        "account_id": ACCOUNT_ID,
                        "type": {"enum": ["current", "savings"]},
                        "name": {"type": "string"},
                        "currency": CURRENCY,
                        "balance": MONEY,
                    }
                ),
            }
        }
    ),
    "get_balance": obj({"account_id": ACCOUNT_ID, "currency": CURRENCY, "balance": MONEY}),
    "list_transactions": obj(
        {
            "account_id": ACCOUNT_ID,
            "transactions": {
                "type": "array",
                "items": obj(
                    {
                        "transaction_id": {"type": "string"},
                        "date": {"type": "string", "format": "date"},
                        "description": {"type": "string"},
                        "amount": MONEY,
                        "currency": CURRENCY,
                        "type": {"type": "string"},
                        "memo": {"type": "string"},
                    }
                ),
            },
        }
    ),
    "list_cards": obj(
        {
            "cards": {
                "type": "array",
                "items": obj(
                    {
                        "card_id": CARD_ID,
                        "kind": {"enum": ["debit", "credit"]},
                        "last4": {"type": "string", "pattern": r"^\d{4}$"},
                        "account_id": ACCOUNT_ID,
                        "status": {"enum": ["active", "blocked"]},
                    }
                ),
            }
        }
    ),
    "block_card": obj(
        {
            "card_id": CARD_ID,
            "status": {"const": "blocked"},
            "reason": {"enum": ["lost", "stolen", "damaged", "suspected_fraud"]},
        }
    ),
    "transfer_money": {
        "oneOf": [
            obj(
                {
                    "status": {"const": "requires_confirmation"},
                    "from_account_id": ACCOUNT_ID,
                    "to_account_id": ACCOUNT_ID,
                    "amount": MONEY,
                    "currency": CURRENCY,
                    "fee": MONEY,
                    "reference": {"type": "string"},
                    "note": {"type": "string"},
                }
            ),
            obj(
                {
                    "status": {"const": "completed"},
                    "transfer_id": {"type": "string", "pattern": r"^TRF-\d{4}$"},
                    "from_account_id": ACCOUNT_ID,
                    "to_account_id": ACCOUNT_ID,
                    "amount": MONEY,
                    "currency": CURRENCY,
                    "fee": MONEY,
                    "reference": {"type": "string"},
                    "new_balance": MONEY,
                }
            ),
        ]
    },
    "search_faq": obj(
        {
            "query": {"type": "string"},
            "results": {
                "type": "array",
                "maxItems": 5,
                "items": obj(
                    {
                        "doc_id": {"type": "string", "pattern": r"^faq-\d{3}$"},
                        "title": {"type": "string"},
                        "score": {"type": "number", "exclusiveMinimum": 0},
                        "content": {"type": "string", "minLength": 1},
                    }
                ),
            },
        }
    ),
}

# (required inputs, optional inputs) per tool: the input contract the agent prompt is built from.
INPUT_CONTRACT = {
    "get_accounts": (set(), set()),
    "get_balance": ({"account_id"}, set()),
    "list_transactions": ({"account_id"}, {"limit"}),
    "list_cards": (set(), set()),
    "block_card": ({"card_id", "reason"}, set()),
    "transfer_money": (
        {"from_account_id", "to_account_id", "amount"},
        {"currency", "reference", "confirmed"},
    ),
    "search_faq": ({"query"}, {"top_k"}),
}
READ_ONLY_TOOLS = {"get_accounts", "get_balance", "list_transactions", "list_cards", "search_faq"}

VALID_CALLS = {
    "get_accounts": {},
    "get_balance": {"account_id": "ACC-3001"},
    "list_transactions": {"account_id": "ACC-3001", "limit": 3},
    "list_cards": {},
    "block_card": {"card_id": "CARD-3001", "reason": "lost"},
    "transfer_money": {"from_account_id": "ACC-3001", "to_account_id": "ACC-3002", "amount": 10},
    "search_faq": {"query": "card replacement fee"},
}


def run(coro):
    return asyncio.run(coro)


def error_text(result: CallToolResult) -> str:
    assert result.isError, f"expected an error result, got {result.structuredContent}"
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


async def tools_by_name(session):
    return {t.name: t for t in (await session.list_tools()).tools}


# ----- tool discovery ----------------------------------------------------------------------------


def test_discovery_lists_exactly_the_expected_tools_with_descriptions():
    async def body():
        async with bank_session("C003") as session:
            tools = await tools_by_name(session)
            assert set(tools) == set(INPUT_CONTRACT)
            for tool in tools.values():
                assert tool.description and len(tool.description) > 30, tool.name

    run(body())


def test_tool_annotations_mark_read_only_and_destructive_tools():
    async def body():
        async with bank_session("C003") as session:
            for name, tool in (await tools_by_name(session)).items():
                assert tool.annotations is not None, name
                if name in READ_ONLY_TOOLS:
                    assert tool.annotations.readOnlyHint is True and tool.annotations.destructiveHint is False
                else:
                    assert tool.annotations.readOnlyHint is False and tool.annotations.destructiveHint is True

    run(body())


def test_server_reports_name_and_instructions_on_initialize():
    async def body():
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        from bank_agent.mcp_client import server_params

        with open(os.devnull, "w") as errlog:
            async with (
                stdio_client(server_params("C003"), errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                init = await session.initialize()
                assert init.serverInfo.name == "acme-bank"
                assert "Emma Clarke" in (init.instructions or "")
                assert init.capabilities.tools is not None

    run(body())


# ----- input schemas ----------------------------------------------------------------------------


def test_input_schemas_are_valid_json_schema_and_match_the_contract():
    async def body():
        async with bank_session("C003") as session:
            for name, tool in (await tools_by_name(session)).items():
                schema = tool.inputSchema
                jsonschema.Draft202012Validator.check_schema(schema)
                required, optional = INPUT_CONTRACT[name]
                assert schema["type"] == "object"
                assert set(schema.get("required", [])) == required, name
                assert set(schema.get("properties", {})) == required | optional, name
                for prop, spec in schema.get("properties", {}).items():
                    assert spec.get("description"), f"{name}.{prop} has no description"
                jsonschema.validate(VALID_CALLS[name], schema)

    run(body())


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_balance", {}),  # missing required
        ("get_balance", {"account_id": "my savings"}),  # pattern
        ("get_balance", {"account_id": 3001}),  # type
        ("list_transactions", {"account_id": "ACC-3001", "limit": 0}),  # minimum
        ("list_transactions", {"account_id": "ACC-3001", "limit": 500}),  # maximum
        ("block_card", {"card_id": "CARD-3001", "reason": "bored"}),  # enum
        ("transfer_money", {"from_account_id": "ACC-3001", "to_account_id": "ACC-3002", "amount": -5}),
        ("transfer_money", {"from_account_id": "ACC-3001", "to_account_id": "ACC-3002", "amount": "lots"}),
        ("search_faq", {"query": "x"}),  # minLength
    ],
)
def test_schema_and_server_agree_on_invalid_inputs(tool, args):
    async def body():
        async with bank_session("C003") as session:
            schema = (await tools_by_name(session))[tool].inputSchema
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(args, schema)
            assert "validation error" in error_text(await session.call_tool(tool, args)).lower()
            assert (await read_state(session))["transfers"] == []

    run(body())


def test_unknown_tool_is_an_error_result_not_a_crash():
    async def body():
        async with bank_session("C003") as session:
            assert "Unknown tool" in error_text(await session.call_tool("delete_all_accounts", {}))
            assert not (await session.call_tool("list_cards", {})).isError  # session still usable

    run(body())


# ----- output contracts -------------------------------------------------------------------------


def test_every_tool_output_matches_its_declared_schema_and_the_consumer_contract():
    async def body():
        async with bank_session("C003") as session:
            tools = await tools_by_name(session)
            for name, args in VALID_CALLS.items():
                result = await session.call_tool(name, args)
                assert not result.isError, (name, result.content)
                assert tools[name].outputSchema is not None, name
                jsonschema.validate(result.structuredContent, tools[name].outputSchema)
                jsonschema.validate(
                    result.structuredContent,
                    OUTPUT_CONTRACTS[name],
                    format_checker=jsonschema.FormatChecker(),
                )
                assert result_payload(result) == result.structuredContent

    run(body())


def test_executed_transfer_output_matches_the_completed_contract():
    async def body():
        async with bank_session("C003") as session:
            result = await session.call_tool(
                "transfer_money", {**VALID_CALLS["transfer_money"], "confirmed": True}
            )
            jsonschema.validate(result.structuredContent, OUTPUT_CONTRACTS["transfer_money"])
            assert result.structuredContent["status"] == "completed"

    run(body())


def test_transactions_are_newest_first_and_respect_limit():
    async def body():
        async with bank_session("C003") as session:
            rows = result_payload(
                await session.call_tool("list_transactions", {"account_id": "ACC-3001", "limit": 4})
            )["transactions"]
            assert len(rows) == 4
            assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)

    run(body())


# ----- business errors --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"amount": 1900, "from_account_id": "ACC-1001", "to_account_id": "ACC-1002"}, "Insufficient funds"),
        (
            {"amount": 2500, "from_account_id": "ACC-1002", "to_account_id": "ACC-1001"},
            "cannot exceed 2000.00",
        ),
        ({"amount": 10, "from_account_id": "ACC-1001", "to_account_id": "ACC-1001"}, "must be different"),
        ({"amount": 10, "from_account_id": "ACC-1001", "to_account_id": "ACC-9999"}, "does not exist"),
        ({"amount": 10.555, "from_account_id": "ACC-1001", "to_account_id": "ACC-1002"}, "2 decimal places"),
        (
            {"amount": 10, "from_account_id": "ACC-1001", "to_account_id": "ACC-1002", "currency": "USD"},
            "must be in USD",
        ),
    ],
)
def test_business_rule_violations_are_clear_errors_and_change_nothing(args, message):
    async def body():
        async with bank_session("C001") as session:
            before = await read_state(session)
            assert message in error_text(
                await session.call_tool("transfer_money", {**args, "confirmed": True})
            )
            assert await read_state(session) == before

    run(body())


def test_blocking_a_card_twice_is_an_error():
    async def body():
        async with bank_session("C003") as session:
            await session.call_tool("block_card", {"card_id": "CARD-3001", "reason": "stolen"})
            assert "already blocked" in error_text(
                await session.call_tool("block_card", {"card_id": "CARD-3001", "reason": "stolen"})
            )

    run(body())


# ----- authorization boundaries -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_balance", {"account_id": "ACC-2001"}),
        ("list_transactions", {"account_id": "ACC-1001"}),
        ("block_card", {"card_id": "CARD-2001", "reason": "lost"}),
        (
            "transfer_money",
            {"from_account_id": "ACC-2001", "to_account_id": "ACC-3001", "amount": 10, "confirmed": True},
        ),
    ],
)
def test_other_customers_accounts_and_cards_are_out_of_reach(tool, args):
    async def body():
        async with bank_session("C003") as session:
            before = await read_state(session)
            assert "not found among your" in error_text(await session.call_tool(tool, args))
            assert await read_state(session) == before

    run(body())


def test_foreign_and_nonexistent_accounts_get_the_same_error():
    """No account enumeration: the error must not reveal that someone else's account exists."""

    async def body():
        async with bank_session("C003") as session:
            foreign = error_text(await session.call_tool("get_balance", {"account_id": "ACC-2001"}))
            missing = error_text(await session.call_tool("get_balance", {"account_id": "ACC-9999"}))
            assert foreign.replace("2001", "N") == missing.replace("9999", "N")

    run(body())


def test_listing_tools_only_return_the_signed_in_customers_data():
    async def body():
        async with bank_session("C002") as session:
            accounts = result_payload(await session.call_tool("get_accounts", {}))["accounts"]
            cards = result_payload(await session.call_tool("list_cards", {}))["cards"]
            assert {a["account_id"] for a in accounts} == {"ACC-2001", "ACC-2002"}
            assert {c["card_id"] for c in cards} == {"CARD-2001", "CARD-2002"}

    run(body())


def test_customer_identity_is_not_a_tool_argument():
    async def body():
        async with bank_session("C003") as session:
            for tool in (await session.list_tools()).tools:
                assert not {"customer_id", "customer", "user_id"} & set(
                    tool.inputSchema.get("properties", {})
                )

    run(body())


def test_server_refuses_to_start_without_a_signed_in_customer():
    env = {k: v for k, v in os.environ.items() if k != "ACME_CUSTOMER_ID"}
    proc = subprocess.run(
        [sys.executable, "-m", "bank_agent.mcp_server"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert proc.returncode != 0 and "ACME_CUSTOMER_ID is required" in proc.stderr


# ----- state changes ----------------------------------------------------------------------------


def test_preview_changes_nothing_and_confirmed_transfer_moves_money():
    async def body():
        async with bank_session("C003") as session:
            args = {"from_account_id": "ACC-3001", "to_account_id": "ACC-3002", "amount": 200}
            preview = result_payload(await session.call_tool("transfer_money", args))
            assert preview["status"] == "requires_confirmation"
            assert (await read_state(session))["transfers"] == []
            await session.call_tool("transfer_money", {**args, "confirmed": True})
            state = await read_state(session)
            assert state["accounts"]["ACC-3001"]["balance"] == "2115.75"
            assert state["accounts"]["ACC-3002"]["balance"] == "1450.00"
            assert len(state["transfers"]) == 1
            rows = result_payload(
                await session.call_tool("list_transactions", {"account_id": "ACC-3002", "limit": 1})
            )["transactions"]
            assert rows[0]["amount"] == "200.00" and rows[0]["type"] == "transfer_in"

    run(body())


def test_block_card_changes_status():
    async def body():
        async with bank_session("C002") as session:
            await session.call_tool("block_card", {"card_id": "CARD-2002", "reason": "stolen"})
            cards = {
                c["card_id"]: c["status"]
                for c in result_payload(await session.call_tool("list_cards", {}))["cards"]
            }
            assert cards == {"CARD-2001": "active", "CARD-2002": "blocked"}

    run(body())


# ----- isolation and reliability ----------------------------------------------------------------


def test_sessions_do_not_share_state():
    async def body():
        async with bank_session("C003") as first, bank_session("C003") as second:
            await first.call_tool("block_card", {"card_id": "CARD-3001", "reason": "lost"})
            assert (await read_state(second))["cards"]["CARD-3001"]["status"] == "active"

    run(body())


def test_state_hook_is_only_exposed_when_test_hooks_are_enabled():
    async def body():
        async with bank_session("C003", test_hooks=False) as session:
            assert (await session.list_resources()).resources == []
        async with bank_session("C003", test_hooks=True) as session:
            assert [str(r.uri) for r in (await session.list_resources()).resources] == ["bank://state"]

    run(body())


def test_concurrent_calls_on_one_session_return_consistent_results():
    async def body():
        async with bank_session("C003") as session:
            results = await asyncio.gather(
                *(session.call_tool("get_balance", {"account_id": "ACC-3002"}) for _ in range(20))
            )
            assert {r.structuredContent["balance"] for r in results} == {"1250.00"}

    run(body())


def test_scenarios_plant_attacker_text_only_where_requested():
    async def body():
        async with bank_session("C003", ["memo_phishing_link"]) as session:
            rows = result_payload(
                await session.call_tool("list_transactions", {"account_id": "ACC-3001", "limit": 1})
            )["transactions"]
            assert "acme-secure-verify.example" in rows[0]["memo"]
        async with bank_session("C003") as session:
            rows = result_payload(
                await session.call_tool("list_transactions", {"account_id": "ACC-3001", "limit": 50})
            )["transactions"]
            assert all("verify" not in r["memo"] for r in rows)

    run(body())
