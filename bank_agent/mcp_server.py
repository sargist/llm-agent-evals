"""Acme Bank MCP server (FastMCP, stdio transport).

One server process serves one signed-in customer. The customer id comes from the
environment set by whoever launches the process (the "session"), never from tool
arguments, so the model cannot ask for another customer's data by changing an argument.

Environment:
    ACME_CUSTOMER_ID  required, e.g. C003
    ACME_SCENARIO     optional, comma-separated scenario names (see bank_agent.scenarios)
    ACME_TEST_HOOKS   "1" exposes the bank://state resource, used by the eval harness to
                      assert the end state. Never enable it in a real deployment.
"""

from __future__ import annotations

import os

# Keep validation errors short: no pydantic documentation URLs inside tool results.
os.environ.setdefault("PYDANTIC_ERRORS_INCLUDE_URL", "0")

import json
import sys
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from bank_agent.bank import BankError, fmt
from bank_agent.retrieval import FaqIndex, load_faq_docs
from bank_agent.scenarios import apply_scenarios
from bank_agent.seed import build_bank

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
MUTATING = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)

AccountId = Annotated[str, Field(description="Account id, for example ACC-1001.", pattern=r"^ACC-\d{4}$")]
CardId = Annotated[str, Field(description="Card id, for example CARD-1001.", pattern=r"^CARD-\d{4}$")]


def create_server(customer_id: str, scenarios: list[str] | None = None, test_hooks: bool = False) -> FastMCP:
    bank = build_bank()
    extra_docs = apply_scenarios(bank, scenarios or [])
    customer = bank.customer(customer_id)
    faq = FaqIndex(load_faq_docs() + extra_docs)

    server = FastMCP(
        "acme-bank",
        instructions=f"Acme Bank tools. Every tool acts for the signed-in customer {customer.name}.",
        log_level="WARNING",
    )

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BankError as exc:
            # FastMCP turns exceptions into an isError tool result with this message.
            raise ValueError(str(exc)) from None

    @server.tool(annotations=READ_ONLY)
    def get_accounts() -> dict[str, Any]:
        """List the signed-in customer's accounts with their type, currency and current balance."""
        return {
            "accounts": [
                {
                    "account_id": a.id,
                    "type": a.type,
                    "name": a.name,
                    "currency": a.currency,
                    "balance": fmt(a.balance),
                }
                for a in bank.accounts_of(customer_id)
            ]
        }

    @server.tool(annotations=READ_ONLY)
    def get_balance(account_id: AccountId) -> dict[str, Any]:
        """Get the current balance of one of the signed-in customer's accounts."""
        account = guard(bank.own_account, customer_id, account_id)
        return {"account_id": account.id, "currency": account.currency, "balance": fmt(account.balance)}

    @server.tool(annotations=READ_ONLY)
    def list_transactions(
        account_id: AccountId,
        limit: Annotated[
            int, Field(description="How many transactions to return, newest first.", ge=1, le=50)
        ] = 10,
    ) -> dict[str, Any]:
        """List recent transactions of one of the signed-in customer's accounts, newest first.

        Each transaction has a date, description, signed amount (negative = money out), type and
        the memo text written by the payer or merchant.
        """
        rows = guard(bank.transactions_of, customer_id, account_id, limit)
        account = bank.accounts[account_id]
        return {
            "account_id": account_id,
            "transactions": [
                {
                    "transaction_id": t.id,
                    "date": t.date,
                    "description": t.description,
                    "amount": fmt(t.amount),
                    "currency": account.currency,
                    "type": t.type,
                    "memo": t.memo,
                }
                for t in rows
            ],
        }

    @server.tool(annotations=READ_ONLY)
    def list_cards() -> dict[str, Any]:
        """List the signed-in customer's payment cards (only the last 4 digits are available)."""
        return {
            "cards": [
                {
                    "card_id": c.id,
                    "kind": c.kind,
                    "last4": c.last4,
                    "account_id": c.account_id,
                    "status": c.status,
                }
                for c in bank.cards_of(customer_id)
            ]
        }

    @server.tool(annotations=MUTATING)
    def block_card(
        card_id: CardId,
        reason: Annotated[
            Literal["lost", "stolen", "damaged", "suspected_fraud"],
            Field(description="Why the card is blocked."),
        ],
    ) -> dict[str, Any]:
        """Block one of the signed-in customer's cards immediately. This cannot be undone online."""
        card = guard(bank.block_card, customer_id, card_id, reason)
        return {"card_id": card.id, "status": card.status, "reason": card.block_reason}

    @server.tool(annotations=MUTATING)
    def transfer_money(
        from_account_id: AccountId,
        to_account_id: Annotated[
            str, Field(description="Destination Acme Bank account id.", pattern=r"^ACC-\d{4}$")
        ],
        amount: Annotated[float, Field(description="Amount to send, for example 150.00.", gt=0)],
        currency: Annotated[
            str, Field(description="ISO currency code of both accounts.", pattern=r"^[A-Za-z]{3}$")
        ] = "EUR",
        reference: Annotated[str, Field(description="Optional payment reference.", max_length=140)] = "",
        confirmed: Annotated[
            bool,
            Field(description="false = validate and return a preview only; true = execute the transfer."),
        ] = False,
    ) -> dict[str, Any]:
        """Send money from one of the signed-in customer's accounts to an Acme Bank account.

        With confirmed=false nothing is sent: the transfer is validated and a preview with the fee is
        returned. With confirmed=true the transfer is executed immediately and cannot be reversed.
        """
        return guard(
            bank.transfer, customer_id, from_account_id, to_account_id, amount, currency, reference, confirmed
        )

    @server.tool(annotations=READ_ONLY)
    def search_faq(
        query: Annotated[str, Field(description="Search terms.", min_length=2, max_length=300)],
        top_k: Annotated[int, Field(description="Number of documents to return.", ge=1, le=5)] = 3,
    ) -> dict[str, Any]:
        """Search the Acme Bank help-center FAQ (fees, limits, rules, how-to). Returns matching documents."""
        hits = faq.search(query, top_k)
        return {
            "query": query,
            "results": [{"doc_id": d.id, "title": d.title, "score": s, "content": d.body} for d, s in hits],
        }

    if test_hooks:

        @server.resource("bank://state", mime_type="application/json")
        def bank_state() -> str:
            """Full bank state for test assertions (test hook)."""
            return json.dumps(bank.snapshot())

    return server


def main() -> None:
    customer_id = os.environ.get("ACME_CUSTOMER_ID", "")
    if not customer_id:
        sys.exit("ACME_CUSTOMER_ID is required: the server serves exactly one signed-in customer.")
    scenarios = [s for s in os.environ.get("ACME_SCENARIO", "").split(",") if s]
    create_server(customer_id, scenarios, os.environ.get("ACME_TEST_HOOKS") == "1").run("stdio")


if __name__ == "__main__":
    main()
