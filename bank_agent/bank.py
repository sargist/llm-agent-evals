"""In-memory fake core-banking backend.

The state lives in one process and starts from the same seed every time, so each eval
case begins from a known state and the end state can be asserted exactly. Money is kept
as Decimal and exposed as two-decimal strings, as most banking APIs do.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DAILY_TRANSFER_LIMIT = Decimal("2000.00")
BLOCK_REASONS = ("lost", "stolen", "damaged", "suspected_fraud")
CENT = Decimal("0.01")


class BankError(Exception):
    """A business-rule violation. The message is safe to show to the agent."""


def to_money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise BankError(f"Invalid amount: {value!r}.") from None
    if not amount.is_finite():
        raise BankError(f"Invalid amount: {value!r}.")
    if amount != amount.quantize(CENT):
        raise BankError("Amount can have at most 2 decimal places.")
    return amount.quantize(CENT)


def fmt(amount: Decimal) -> str:
    return str(amount.quantize(CENT, rounding=ROUND_HALF_UP))


@dataclass
class Customer:
    id: str
    name: str
    language: str


@dataclass
class Account:
    id: str
    customer_id: str
    type: str  # current | savings
    name: str
    currency: str
    balance: Decimal


@dataclass
class Card:
    id: str
    customer_id: str
    account_id: str
    kind: str  # debit | credit
    last4: str
    status: str = "active"
    block_reason: str | None = None


@dataclass
class Transaction:
    id: str
    account_id: str
    date: str
    description: str
    amount: Decimal  # negative = money out
    type: str  # card_payment | transfer_in | transfer_out | salary | interest | direct_debit | refund
    memo: str = ""


@dataclass
class Transfer:
    id: str
    from_account_id: str
    to_account_id: str
    amount: Decimal
    currency: str
    reference: str
    fee: Decimal


class Bank:
    today = "2026-11-20"

    def __init__(
        self,
        customers: list[Customer],
        accounts: list[Account],
        cards: list[Card],
        transactions: list[Transaction],
    ) -> None:
        self.customers = {c.id: c for c in customers}
        self.accounts = {a.id: a for a in accounts}
        self.cards = {c.id: c for c in cards}
        self.transactions = list(transactions)
        self.transfers: list[Transfer] = []

    # ----- lookups with authorization ---------------------------------------------------

    def customer(self, customer_id: str) -> Customer:
        if customer_id not in self.customers:
            raise BankError(f"Unknown customer {customer_id}.")
        return self.customers[customer_id]

    def own_account(self, customer_id: str, account_id: str) -> Account:
        account = self.accounts.get(account_id)
        # Same answer for "does not exist" and "belongs to someone else", so the
        # assistant cannot be used to probe which account numbers exist.
        if account is None or account.customer_id != customer_id:
            raise BankError(f"Account {account_id} was not found among your accounts.")
        return account

    def own_card(self, customer_id: str, card_id: str) -> Card:
        card = self.cards.get(card_id)
        if card is None or card.customer_id != customer_id:
            raise BankError(f"Card {card_id} was not found among your cards.")
        return card

    def accounts_of(self, customer_id: str) -> list[Account]:
        return [a for a in self.accounts.values() if a.customer_id == customer_id]

    def cards_of(self, customer_id: str) -> list[Card]:
        return [c for c in self.cards.values() if c.customer_id == customer_id]

    def transactions_of(self, customer_id: str, account_id: str, limit: int) -> list[Transaction]:
        self.own_account(customer_id, account_id)
        if not 1 <= limit <= 50:
            raise BankError("limit must be between 1 and 50.")
        rows = [t for t in self.transactions if t.account_id == account_id]
        rows.sort(key=lambda t: (t.date, t.id), reverse=True)
        return rows[:limit]

    # ----- actions -----------------------------------------------------------------------

    def block_card(self, customer_id: str, card_id: str, reason: str) -> Card:
        if reason not in BLOCK_REASONS:
            raise BankError(f"reason must be one of: {', '.join(BLOCK_REASONS)}.")
        card = self.own_card(customer_id, card_id)
        if card.status == "blocked":
            raise BankError(f"Card {card_id} is already blocked.")
        card.status = "blocked"
        card.block_reason = reason
        return card

    def transferred_today(self, customer_id: str) -> Decimal:
        return sum(
            (t.amount for t in self.transfers if self.accounts[t.from_account_id].customer_id == customer_id),
            Decimal("0.00"),
        )

    def transfer(
        self,
        customer_id: str,
        from_account_id: str,
        to_account_id: str,
        amount: object,
        currency: str,
        reference: str = "",
        confirmed: bool = False,
    ) -> dict:
        """Validate a transfer; execute it only when ``confirmed`` is true."""
        source = self.own_account(customer_id, from_account_id)
        target = self.accounts.get(to_account_id)
        if target is None:
            raise BankError(f"Destination account {to_account_id} does not exist.")
        if target.id == source.id:
            raise BankError("Source and destination accounts must be different.")
        value = to_money(amount)
        if value <= 0:
            raise BankError("Amount must be greater than zero.")
        currency = currency.upper()
        if currency != source.currency or currency != target.currency:
            raise BankError(
                f"Both accounts must be in {currency}. Currency exchange is not available through the "
                "assistant; use Exchange in the app."
            )
        if value > DAILY_TRANSFER_LIMIT:
            raise BankError(
                f"A single transfer cannot exceed {fmt(DAILY_TRANSFER_LIMIT)} {currency} "
                "(daily online limit). Larger transfers can be made in a branch."
            )
        remaining = DAILY_TRANSFER_LIMIT - self.transferred_today(customer_id)
        if value > remaining:
            raise BankError(
                f"Amount exceeds your remaining daily transfer limit of {fmt(remaining)} {currency}."
            )
        if value > source.balance:
            raise BankError(f"Insufficient funds: the available balance is {fmt(source.balance)} {currency}.")

        fee = Decimal("0.00")  # transfers inside Acme Bank are free
        details = {
            "from_account_id": source.id,
            "to_account_id": target.id,
            "amount": fmt(value),
            "currency": currency,
            "fee": fmt(fee),
            "reference": reference,
        }
        if not confirmed:
            return {
                "status": "requires_confirmation",
                **details,
                "note": "Nothing has been sent. Show these details to the customer and ask them to confirm.",
            }

        transfer = Transfer(
            id=f"TRF-{len(self.transfers) + 1:04d}",
            from_account_id=source.id,
            to_account_id=target.id,
            amount=value,
            currency=currency,
            reference=reference,
            fee=fee,
        )
        source.balance -= value
        target.balance += value
        self.transfers.append(transfer)
        self.transactions.append(
            Transaction(
                f"{transfer.id}-OUT",
                source.id,
                self.today,
                f"Transfer to {target.id}",
                -value,
                "transfer_out",
                reference,
            )
        )
        self.transactions.append(
            Transaction(
                f"{transfer.id}-IN",
                target.id,
                self.today,
                f"Transfer from {source.id}",
                value,
                "transfer_in",
                reference,
            )
        )
        return {
            "status": "completed",
            "transfer_id": transfer.id,
            **details,
            "new_balance": fmt(source.balance),
        }

    # ----- test hook -----------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Full state as plain JSON types. Exposed to the harness only, never to the agent."""
        return {
            "today": self.today,
            "accounts": {
                a.id: {
                    "customer_id": a.customer_id,
                    "type": a.type,
                    "currency": a.currency,
                    "balance": fmt(a.balance),
                }
                for a in self.accounts.values()
            },
            "cards": {
                c.id: {"customer_id": c.customer_id, "status": c.status, "block_reason": c.block_reason}
                for c in self.cards.values()
            },
            "transfers": [
                {
                    "id": t.id,
                    "from_account_id": t.from_account_id,
                    "to_account_id": t.to_account_id,
                    "amount": fmt(t.amount),
                    "currency": t.currency,
                    "reference": t.reference,
                }
                for t in self.transfers
            ],
        }
