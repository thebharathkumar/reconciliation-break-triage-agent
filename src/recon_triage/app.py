"""FastAPI service for the reconciliation triage dashboard.

Endpoints:
  POST /api/reconcile              run reconciliation, log every detected break
  GET  /api/breaks                 list breaks
  GET  /api/breaks/{break_id}      get a break, generating its AI recommendation
  POST /api/breaks/{break_id}/decision  record a human decision, mark resolved
  GET  /api/audit/verify           verify the audit chain
  GET  /api/audit                  return the audit entries
  GET  /                           serve the review dashboard

State is held in memory. The CSVs are generated on demand if absent.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agent
from .audit import (
    EVENT_AI_RECOMMENDATION,
    EVENT_BREAK_DETECTED,
    EVENT_HUMAN_DECISION,
    AuditLog,
)
from .matcher import reconcile
from .schemas import Break, Decision, DecisionType, RecommendedAction, Side, Transaction

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
LEDGER_CSV = DATA_DIR / "ledger.csv"
BANK_CSV = DATA_DIR / "bank.csv"
SOP_PATH = DATA_DIR / "recon_sop.md"
AUDIT_PATH = ROOT / "audit_log.jsonl"

app = FastAPI(title="Reconciliation Break Triage Agent")

# In-memory state for the running service.
_state: dict[str, object] = {"breaks": {}}
_audit = AuditLog(AUDIT_PATH)


def _load_sop() -> str:
    if SOP_PATH.exists():
        return SOP_PATH.read_text(encoding="utf-8")
    return ""


def _ensure_csvs() -> None:
    if LEDGER_CSV.exists() and BANK_CSV.exists():
        return
    # Generate the seed data reproducibly.
    subprocess.run(
        [sys.executable, str(DATA_DIR / "seed_data.py")],
        check=True,
    )


def _read_transactions(path: Path, side: Side) -> list[Transaction]:
    rows: list[Transaction] = []
    with path.open("r", encoding="utf-8") as handle:
        for r in csv.DictReader(handle):
            rows.append(
                Transaction(
                    txn_id=r["txn_id"],
                    settlement_date=date.fromisoformat(r["settlement_date"]),
                    amount=Decimal(r["amount"]),
                    currency=r["currency"],
                    reference=r["reference"],
                    counterparty=r["counterparty"],
                    side=side,
                )
            )
    return rows


def _break_summary(brk: Break) -> dict:
    """A compact view for the breaks table."""

    return {
        "break_id": brk.break_id,
        "break_type": brk.break_type.value,
        "confidence": brk.confidence,
        "resolved": brk.resolved,
        "ledger_amount": _amt(brk.ledger_txn),
        "bank_amount": _amt(brk.bank_txn),
        "currency": _currency(brk),
        "reference": _reference(brk),
        "has_recommendation": brk.recommendation is not None,
        "has_decision": brk.decision is not None,
    }


def _amt(txn: Optional[Transaction]) -> Optional[str]:
    return str(txn.amount) if txn is not None else None


def _currency(brk: Break) -> Optional[str]:
    if brk.ledger_txn is not None:
        return brk.ledger_txn.currency
    if brk.bank_txn is not None:
        return brk.bank_txn.currency
    return None


def _reference(brk: Break) -> Optional[str]:
    if brk.ledger_txn is not None:
        return brk.ledger_txn.reference
    if brk.bank_txn is not None:
        return brk.bank_txn.reference
    return None


class DecisionRequest(BaseModel):
    actor: str
    decision: DecisionType
    corrected_action: Optional[RecommendedAction] = None
    notes: Optional[str] = None


@app.post("/api/reconcile")
def run_reconciliation() -> dict:
    """Load both books, reconcile, and log every detected break."""

    _ensure_csvs()
    ledger = _read_transactions(LEDGER_CSV, Side.LEDGER)
    bank = _read_transactions(BANK_CSV, Side.BANK)
    result = reconcile(ledger, bank)

    breaks: dict[str, Break] = {}
    for brk in result.breaks:
        breaks[brk.break_id] = brk
        _audit.append(
            EVENT_BREAK_DETECTED,
            {
                "break_id": brk.break_id,
                "break_type": brk.break_type.value,
                "confidence": brk.confidence,
                "detail": brk.detail,
            },
        )
    _state["breaks"] = breaks

    by_type: dict[str, int] = {}
    for brk in result.breaks:
        by_type[brk.break_type.value] = by_type.get(brk.break_type.value, 0) + 1

    return {
        "ledger_rows": len(ledger),
        "bank_rows": len(bank),
        "matched_pairs": len(result.matched_pairs),
        "break_count": result.break_count,
        "by_type": by_type,
    }


@app.get("/api/breaks")
def list_breaks() -> dict:
    breaks: dict[str, Break] = _state["breaks"]  # type: ignore[assignment]
    return {"breaks": [_break_summary(b) for b in breaks.values()]}


@app.get("/api/breaks/{break_id}")
def get_break(break_id: str) -> dict:
    """Return a break with its AI recommendation, generating it on first view."""

    breaks: dict[str, Break] = _state["breaks"]  # type: ignore[assignment]
    brk = breaks.get(break_id)
    if brk is None:
        raise HTTPException(status_code=404, detail="break not found")

    if brk.recommendation is None:
        recommendation = agent.generate_recommendation(brk, _load_sop())
        brk.recommendation = recommendation
        _audit.append(
            EVENT_AI_RECOMMENDATION,
            {
                "break_id": brk.break_id,
                "recommended_action": recommendation.recommended_action.value,
                "explanation": recommendation.explanation,
                "likely_root_cause": recommendation.likely_root_cause,
                "proposed_resolution": recommendation.proposed_resolution,
                "referenced_fields": recommendation.referenced_fields,
                "sop_reference": recommendation.sop_reference,
            },
        )

    return brk.model_dump(mode="json")


@app.post("/api/breaks/{break_id}/decision")
def submit_decision(break_id: str, body: DecisionRequest) -> dict:
    """Record a human decision and mark the break resolved."""

    breaks: dict[str, Break] = _state["breaks"]  # type: ignore[assignment]
    brk = breaks.get(break_id)
    if brk is None:
        raise HTTPException(status_code=404, detail="break not found")
    if brk.recommendation is None:
        raise HTTPException(
            status_code=400,
            detail="generate the AI recommendation before deciding",
        )
    if body.decision == DecisionType.CORRECT and body.corrected_action is None:
        raise HTTPException(
            status_code=400,
            detail="a corrected decision must include corrected_action",
        )

    decision = Decision(
        actor=body.actor,
        decision=body.decision,
        timestamp=datetime.now(timezone.utc),
        corrected_action=body.corrected_action,
        notes=body.notes,
    )
    brk.decision = decision
    brk.resolved = True

    _audit.append(
        EVENT_HUMAN_DECISION,
        {
            "break_id": brk.break_id,
            "actor": decision.actor,
            "decision": decision.decision.value,
            "corrected_action": (
                decision.corrected_action.value
                if decision.corrected_action is not None
                else None
            ),
            "notes": decision.notes,
            "timestamp": decision.timestamp.isoformat(),
        },
    )

    return brk.model_dump(mode="json")


@app.get("/api/audit/verify")
def verify_audit() -> dict:
    broken_index = _audit.verify_chain()
    return {
        "intact": broken_index is None,
        "first_broken_index": broken_index,
        "entry_count": len(_audit.entries()),
    }


@app.get("/api/audit")
def get_audit() -> dict:
    return {"entries": _audit.entries()}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
