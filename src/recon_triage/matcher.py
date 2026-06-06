"""Deterministic reconciliation matching, then a fuzzy second pass.

No LLM is used here. This is pure, testable logic: the agent only ever sees
breaks that this module has already classified. The order of operations is
deliberate:

1. Detect duplicates within each book (same reference, amount, currency, date
   appearing more than once on one side).
2. Deterministic match: exact reference, amount, and currency, with settlement
   dates inside a settlement window. These reconcile cleanly and produce no
   break.
3. Fuzzy match on the remainder, sharing a normalized reference: classify
   amount mismatches (fee deductions), date lags (T+1 or T+2 settlement), and
   currency mismatches, each with a confidence score.
4. Anything still unmatched is missing on one side.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .schemas import Break, BreakType, Side, Transaction

# Default settlement window: a clean match may settle up to two business days
# apart. Beyond a same-day match this is reported as a date lag.
DEFAULT_SETTLEMENT_WINDOW_DAYS = 2

# A near-amount within this relative tolerance is treated as a fee or charge
# difference (AMOUNT_MISMATCH) rather than two unrelated payments.
DEFAULT_AMOUNT_TOLERANCE = Decimal("0.05")


def normalize_reference(reference: str) -> str:
    """Normalize a payment reference for comparison.

    Upper-cases, strips, and removes internal whitespace and common separators
    so that "INV-1001", "inv 1001", and "INV1001" all compare equal.
    """

    cleaned = reference.strip().upper()
    for sep in (" ", "-", "_", "/", "."):
        cleaned = cleaned.replace(sep, "")
    return cleaned


@dataclass
class ReconResult:
    """The output of a reconciliation run."""

    matched_pairs: list[tuple[Transaction, Transaction]] = field(default_factory=list)
    breaks: list[Break] = field(default_factory=list)

    @property
    def break_count(self) -> int:
        return len(self.breaks)


def _exact_key(txn: Transaction) -> tuple[str, Decimal, str]:
    return (normalize_reference(txn.reference), txn.amount, txn.currency)


def _dup_key(txn: Transaction) -> tuple[str, Decimal, str, date]:
    return (
        normalize_reference(txn.reference),
        txn.amount,
        txn.currency,
        txn.settlement_date,
    )


def _detect_duplicates(
    transactions: list[Transaction], next_id, breaks: list[Break]
) -> list[Transaction]:
    """Pull out duplicate lines on one side, returning the deduplicated pool.

    The first occurrence of an identical line is kept for matching; every later
    occurrence is reported as a DUPLICATE break.
    """

    seen: dict[tuple, Transaction] = {}
    survivors: list[Transaction] = []
    for txn in transactions:
        key = _dup_key(txn)
        if key in seen:
            original = seen[key]
            side_label = "ledger" if txn.side == Side.LEDGER else "bank"
            breaks.append(
                Break(
                    break_id=next_id(),
                    break_type=BreakType.DUPLICATE,
                    confidence=0.95,
                    ledger_txn=txn if txn.side == Side.LEDGER else None,
                    bank_txn=txn if txn.side == Side.BANK else None,
                    detail=(
                        f"Duplicate {side_label} entry: reference "
                        f"{txn.reference} for {txn.amount} {txn.currency} on "
                        f"{txn.settlement_date.isoformat()} also appears as "
                        f"{original.txn_id}."
                    ),
                )
            )
        else:
            seen[key] = txn
            survivors.append(txn)
    return survivors


def _classify_fuzzy(
    ledger_txn: Transaction, bank_txn: Transaction, window_days: int
) -> tuple[BreakType, float, str] | None:
    """Classify a candidate ledger/bank pair sharing a normalized reference.

    Returns the break type, a confidence score, and a human-readable detail, or
    None if the pair is not a plausible fuzzy match.
    """

    ref = ledger_txn.reference
    same_amount = ledger_txn.amount == bank_txn.amount
    same_currency = ledger_txn.currency == bank_txn.currency
    day_gap = abs((ledger_txn.settlement_date - bank_txn.settlement_date).days)

    # Date lag: identical money, settled a day or two apart.
    if same_amount and same_currency and 1 <= day_gap <= window_days:
        confidence = 0.9 if day_gap == 1 else 0.8
        return (
            BreakType.DATE_LAG,
            confidence,
            (
                f"Reference {ref} settles {day_gap} day(s) apart: ledger "
                f"{ledger_txn.settlement_date.isoformat()} vs bank "
                f"{bank_txn.settlement_date.isoformat()}. Amount and currency "
                "agree."
            ),
        )

    # Currency mismatch: same amount and date, different currency code.
    if same_amount and not same_currency and day_gap <= window_days:
        return (
            BreakType.CURRENCY_MISMATCH,
            0.85,
            (
                f"Reference {ref} has matching amount {ledger_txn.amount} but "
                f"differing currency: ledger {ledger_txn.currency} vs bank "
                f"{bank_txn.currency}."
            ),
        )

    # Amount mismatch: same currency and close dates, amount differs within a
    # fee-sized tolerance.
    if same_currency and day_gap <= window_days and not same_amount:
        larger = max(abs(ledger_txn.amount), abs(bank_txn.amount))
        if larger == 0:
            return None
        relative = abs(ledger_txn.amount - bank_txn.amount) / larger
        if relative <= DEFAULT_AMOUNT_TOLERANCE:
            # Higher confidence when the gap is smaller relative to the amount.
            confidence = round(float(1 - relative), 2)
            delta = ledger_txn.amount - bank_txn.amount
            return (
                BreakType.AMOUNT_MISMATCH,
                confidence,
                (
                    f"Reference {ref} differs by {delta} {ledger_txn.currency} "
                    f"(ledger {ledger_txn.amount} vs bank {bank_txn.amount}), "
                    "consistent with a fee or charge deduction."
                ),
            )

    return None


def reconcile(
    ledger: list[Transaction],
    bank: list[Transaction],
    settlement_window_days: int = DEFAULT_SETTLEMENT_WINDOW_DAYS,
) -> ReconResult:
    """Reconcile a ledger against a bank statement and classify every break."""

    result = ReconResult()
    counter = {"n": 0}

    def next_id() -> str:
        counter["n"] += 1
        return f"BRK-{counter['n']:04d}"

    # 1. Duplicates within each book.
    ledger_pool = _detect_duplicates(list(ledger), next_id, result.breaks)
    bank_pool = _detect_duplicates(list(bank), next_id, result.breaks)

    # 2. Deterministic exact match: identical reference, amount, currency, and
    #    settlement date. A same-day match reconciles cleanly. Settlement-date
    #    differences are intentionally left for the fuzzy pass to surface as
    #    DATE_LAG breaks (T+1 or T+2 settlement is worth reporting, not hiding).
    bank_by_key: dict[tuple, list[Transaction]] = defaultdict(list)
    for txn in bank_pool:
        bank_by_key[_exact_key(txn)].append(txn)

    unmatched_ledger: list[Transaction] = []
    matched_bank_ids: set[str] = set()
    for ledger_txn in ledger_pool:
        candidates = bank_by_key.get(_exact_key(ledger_txn), [])
        match = None
        for cand in candidates:
            if cand.txn_id in matched_bank_ids:
                continue
            if ledger_txn.settlement_date == cand.settlement_date:
                match = cand
                break
        if match is not None:
            matched_bank_ids.add(match.txn_id)
            result.matched_pairs.append((ledger_txn, match))
        else:
            unmatched_ledger.append(ledger_txn)

    unmatched_bank = [t for t in bank_pool if t.txn_id not in matched_bank_ids]

    # 3. Fuzzy pass on the remainder, grouped by normalized reference.
    bank_by_ref: dict[str, list[Transaction]] = defaultdict(list)
    for txn in unmatched_bank:
        bank_by_ref[normalize_reference(txn.reference)].append(txn)

    used_bank_ids: set[str] = set()
    still_unmatched_ledger: list[Transaction] = []
    for ledger_txn in unmatched_ledger:
        ref = normalize_reference(ledger_txn.reference)
        best: tuple[Transaction, BreakType, float, str] | None = None
        for cand in bank_by_ref.get(ref, []):
            if cand.txn_id in used_bank_ids:
                continue
            classified = _classify_fuzzy(ledger_txn, cand, settlement_window_days)
            if classified is None:
                continue
            break_type, confidence, detail = classified
            if best is None or confidence > best[2]:
                best = (cand, break_type, confidence, detail)
        if best is not None:
            cand, break_type, confidence, detail = best
            used_bank_ids.add(cand.txn_id)
            result.breaks.append(
                Break(
                    break_id=next_id(),
                    break_type=break_type,
                    confidence=confidence,
                    ledger_txn=ledger_txn,
                    bank_txn=cand,
                    detail=detail,
                )
            )
        else:
            still_unmatched_ledger.append(ledger_txn)

    # 4. Whatever is left is missing on one side.
    for ledger_txn in still_unmatched_ledger:
        result.breaks.append(
            Break(
                break_id=next_id(),
                break_type=BreakType.MISSING_IN_BANK,
                confidence=0.7,
                ledger_txn=ledger_txn,
                detail=(
                    f"Ledger entry {ledger_txn.txn_id} (reference "
                    f"{ledger_txn.reference}, {ledger_txn.amount} "
                    f"{ledger_txn.currency}) has no matching bank line."
                ),
            )
        )

    for bank_txn in unmatched_bank:
        if bank_txn.txn_id in used_bank_ids:
            continue
        result.breaks.append(
            Break(
                break_id=next_id(),
                break_type=BreakType.MISSING_IN_LEDGER,
                confidence=0.7,
                bank_txn=bank_txn,
                detail=(
                    f"Bank entry {bank_txn.txn_id} (reference "
                    f"{bank_txn.reference}, {bank_txn.amount} "
                    f"{bank_txn.currency}) has no matching ledger line."
                ),
            )
        )

    return result
