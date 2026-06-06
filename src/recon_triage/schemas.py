"""Pydantic models shared across the reconciliation pipeline.

These are deliberately small and explicit. Money is carried as Decimal so that
fee differences and tolerances are exact, never subject to float drift.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Side(str, Enum):
    """Which book a transaction came from."""

    LEDGER = "ledger"
    BANK = "bank"


class BreakType(str, Enum):
    """The categories of reconciliation break this system detects."""

    MISSING_IN_BANK = "MISSING_IN_BANK"
    MISSING_IN_LEDGER = "MISSING_IN_LEDGER"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DATE_LAG = "DATE_LAG"
    DUPLICATE = "DUPLICATE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"


class RecommendedAction(str, Enum):
    """The actions an operator can take on a break.

    The agent proposes one of these; a human approves, rejects, or corrects it.
    """

    POST_ADJUSTMENT = "post_adjustment"
    AWAIT_SETTLEMENT = "await_settlement"
    ESCALATE = "escalate"
    WRITE_OFF = "write_off"
    MANUAL_REVIEW = "manual_review"


class DecisionType(str, Enum):
    """How a human responded to the agent's recommendation."""

    APPROVE = "approve"
    REJECT = "reject"
    CORRECT = "correct"


class Transaction(BaseModel):
    """A single payment line from either the ledger or the bank statement."""

    txn_id: str
    settlement_date: date
    amount: Decimal
    currency: str
    reference: str
    counterparty: str
    side: Side


class Recommendation(BaseModel):
    """The agent's structured explanation and proposed resolution for a break.

    Grounded only in the break data it is shown plus the resolution policy. The
    agent must reference specific fields and must not invent transactions.
    """

    explanation: str = Field(
        description="Plain-English explanation of what the break is."
    )
    likely_root_cause: str = Field(
        description="The most probable cause, grounded in the data shown."
    )
    proposed_resolution: str = Field(
        description="Concrete steps an operator would take to resolve it."
    )
    recommended_action: RecommendedAction = Field(
        description="One of the allowed operator actions."
    )
    referenced_fields: list[str] = Field(
        default_factory=list,
        description="Specific transaction fields the reasoning relies on.",
    )
    sop_reference: Optional[str] = Field(
        default=None,
        description="The section of the resolution policy this cites, if any.",
    )


class Decision(BaseModel):
    """A human decision recorded against a break."""

    actor: str
    decision: DecisionType
    timestamp: datetime
    corrected_action: Optional[RecommendedAction] = None
    notes: Optional[str] = None


class Break(BaseModel):
    """A detected reconciliation break with its context and lifecycle state."""

    break_id: str
    break_type: BreakType
    confidence: float = Field(ge=0.0, le=1.0)
    ledger_txn: Optional[Transaction] = None
    bank_txn: Optional[Transaction] = None
    detail: str = ""
    resolved: bool = False
    recommendation: Optional[Recommendation] = None
    decision: Optional[Decision] = None
