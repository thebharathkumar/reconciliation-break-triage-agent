"""Tests for the deterministic matcher: each break type is detected correctly."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from recon_triage.matcher import normalize_reference, reconcile
from recon_triage.schemas import BreakType, Side, Transaction


def _ledger(txn_id, ref, amount, currency="USD", day=1, cp="Atlas Clearing"):
    return Transaction(
        txn_id=txn_id,
        settlement_date=date(2026, 5, day),
        amount=Decimal(amount),
        currency=currency,
        reference=ref,
        counterparty=cp,
        side=Side.LEDGER,
    )


def _bank(txn_id, ref, amount, currency="USD", day=1, cp="Atlas Clearing"):
    return Transaction(
        txn_id=txn_id,
        settlement_date=date(2026, 5, day),
        amount=Decimal(amount),
        currency=currency,
        reference=ref,
        counterparty=cp,
        side=Side.BANK,
    )


def _types(result):
    return {b.break_type for b in result.breaks}


def test_clean_pair_produces_no_break():
    ledger = [_ledger("L1", "INV-1", "100.00")]
    bank = [_bank("B1", "INV-1", "100.00")]
    result = reconcile(ledger, bank)
    assert result.break_count == 0
    assert len(result.matched_pairs) == 1


def test_normalize_reference_collapses_separators():
    assert normalize_reference("INV-1001") == "INV1001"
    assert normalize_reference("inv 1001") == "INV1001"
    assert normalize_reference(" INV_1001 ") == "INV1001"


def test_missing_in_bank():
    ledger = [_ledger("L1", "INV-1", "500.00")]
    bank: list[Transaction] = []
    result = reconcile(ledger, bank)
    assert _types(result) == {BreakType.MISSING_IN_BANK}
    assert result.breaks[0].ledger_txn.txn_id == "L1"
    assert result.breaks[0].bank_txn is None


def test_missing_in_ledger():
    ledger: list[Transaction] = []
    bank = [_bank("B1", "INV-1", "500.00")]
    result = reconcile(ledger, bank)
    assert _types(result) == {BreakType.MISSING_IN_LEDGER}
    assert result.breaks[0].bank_txn.txn_id == "B1"
    assert result.breaks[0].ledger_txn is None


def test_amount_mismatch_fee_deduction():
    # Bank received a fee-deducted net amount.
    ledger = [_ledger("L1", "INV-1", "1000.00")]
    bank = [_bank("B1", "INV-1", "985.50")]
    result = reconcile(ledger, bank)
    assert _types(result) == {BreakType.AMOUNT_MISMATCH}
    brk = result.breaks[0]
    assert brk.ledger_txn.txn_id == "L1"
    assert brk.bank_txn.txn_id == "B1"
    assert 0.0 < brk.confidence <= 1.0


def test_large_amount_difference_is_two_missing_not_mismatch():
    # A 50% difference is not a fee; the two lines are unrelated.
    ledger = [_ledger("L1", "INV-1", "1000.00")]
    bank = [_bank("B1", "INV-1", "500.00")]
    result = reconcile(ledger, bank)
    assert _types(result) == {BreakType.MISSING_IN_BANK, BreakType.MISSING_IN_LEDGER}


def test_date_lag():
    ledger = [_ledger("L1", "INV-1", "750.00", day=1)]
    bank = [_bank("B1", "INV-1", "750.00", day=2)]
    result = reconcile(ledger, bank)
    assert _types(result) == {BreakType.DATE_LAG}
    assert result.breaks[0].confidence >= 0.8


def test_date_lag_two_days():
    ledger = [_ledger("L1", "INV-1", "750.00", day=1)]
    bank = [_bank("B1", "INV-1", "750.00", day=3)]
    result = reconcile(ledger, bank)
    assert _types(result) == {BreakType.DATE_LAG}


def test_duplicate_in_ledger():
    ledger = [
        _ledger("L1", "INV-1", "200.00", day=4),
        _ledger("L2", "INV-1", "200.00", day=4),
    ]
    bank = [_bank("B1", "INV-1", "200.00", day=4)]
    result = reconcile(ledger, bank)
    # The second ledger line is a duplicate; the first reconciles cleanly.
    assert _types(result) == {BreakType.DUPLICATE}
    assert len(result.matched_pairs) == 1


def test_currency_mismatch():
    ledger = [_ledger("L1", "INV-1", "300.00", currency="USD")]
    bank = [_bank("B1", "INV-1", "300.00", currency="EUR")]
    result = reconcile(ledger, bank)
    assert _types(result) == {BreakType.CURRENCY_MISMATCH}


def test_all_break_types_in_one_run():
    ledger = [
        _ledger("L0", "OK-1", "100.00"),               # clean
        _ledger("L1", "MB-1", "500.00"),               # missing in bank
        _ledger("L2", "AM-1", "1000.00"),              # amount mismatch
        _ledger("L3", "DL-1", "750.00", day=1),        # date lag
        _ledger("L4", "DUP-1", "200.00", day=4),       # duplicate (with L5)
        _ledger("L5", "DUP-1", "200.00", day=4),
        _ledger("L6", "CM-1", "300.00", currency="USD"),  # currency mismatch
    ]
    bank = [
        _bank("B0", "OK-1", "100.00"),
        _bank("B2", "AM-1", "985.50"),
        _bank("B3", "DL-1", "750.00", day=2),
        _bank("B4", "DUP-1", "200.00", day=4),
        _bank("B6", "CM-1", "300.00", currency="EUR"),
        _bank("B7", "ML-1", "999.00"),                 # missing in ledger
    ]
    result = reconcile(ledger, bank)
    assert _types(result) == {
        BreakType.MISSING_IN_BANK,
        BreakType.MISSING_IN_LEDGER,
        BreakType.AMOUNT_MISMATCH,
        BreakType.DATE_LAG,
        BreakType.DUPLICATE,
        BreakType.CURRENCY_MISMATCH,
    }


def test_break_ids_are_unique():
    ledger = [_ledger("L1", "INV-1", "500.00"), _ledger("L2", "INV-2", "600.00")]
    bank: list[Transaction] = []
    result = reconcile(ledger, bank)
    ids = [b.break_id for b in result.breaks]
    assert len(ids) == len(set(ids))
