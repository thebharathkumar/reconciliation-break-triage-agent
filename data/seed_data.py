"""Generate a reproducible pair of ledgers with a realistic mix of breaks.

Produces data/ledger.csv (internal payments ledger) and data/bank.csv (bank or
nostro statement). Most lines reconcile cleanly. A fixed seed makes the injected
breaks reproducible run to run, so the demo and the tests are stable.

Run directly:

    python data/seed_data.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
SEED = 42
BASE_DATE = date(2026, 5, 1)
CLEAN_ROWS = 130

COUNTERPARTIES = [
    "Meridian Capital",
    "Northwind Bank",
    "Atlas Clearing",
    "Sterling Funds",
    "Pacific Custody",
    "Granite Securities",
    "Halcyon Trust",
    "Vantage Partners",
]


def _amount(rng: random.Random) -> Decimal:
    # Two-decimal amounts, the shape of settlement values.
    whole = rng.randint(1_000, 5_000_000)
    cents = rng.randint(0, 99)
    return Decimal(f"{whole}.{cents:02d}")


def generate() -> tuple[list[dict], list[dict]]:
    """Build the ledger and bank rows. Returns (ledger_rows, bank_rows)."""

    rng = random.Random(SEED)
    ledger: list[dict] = []
    bank: list[dict] = []

    def row(txn_id, settlement_date, amount, currency, reference, counterparty):
        return {
            "txn_id": txn_id,
            "settlement_date": settlement_date.isoformat(),
            "amount": str(amount),
            "currency": currency,
            "reference": reference,
            "counterparty": counterparty,
        }

    # Clean, reconciling pairs.
    for i in range(CLEAN_ROWS):
        ref = f"INV-{10000 + i}"
        amount = _amount(rng)
        currency = rng.choices(["USD", "EUR", "GBP"], weights=[7, 2, 1])[0]
        settlement = BASE_DATE + timedelta(days=rng.randint(0, 20))
        cp = rng.choice(COUNTERPARTIES)
        ledger.append(row(f"L{i:05d}", settlement, amount, currency, ref, cp))
        bank.append(row(f"B{i:05d}", settlement, amount, currency, ref, cp))

    # Injected breaks below. Each block is small and deliberate.
    n = CLEAN_ROWS

    # Missing in bank: ledger recorded it, bank has not reported it.
    for j in range(3):
        ref = f"INV-{20000 + j}"
        amount = _amount(rng)
        settlement = BASE_DATE + timedelta(days=rng.randint(0, 20))
        cp = rng.choice(COUNTERPARTIES)
        ledger.append(row(f"L{n:05d}", settlement, amount, "USD", ref, cp))
        n += 1

    # Missing in ledger: bank reported a movement with no ledger entry.
    for j in range(3):
        ref = f"INV-{21000 + j}"
        amount = _amount(rng)
        settlement = BASE_DATE + timedelta(days=rng.randint(0, 20))
        cp = rng.choice(COUNTERPARTIES)
        bank.append(row(f"B{n:05d}", settlement, amount, "USD", ref, cp))
        n += 1

    # Amount mismatch: a fee or charge deducted on the bank side.
    for j in range(3):
        ref = f"INV-{22000 + j}"
        gross = _amount(rng)
        fee = Decimal(f"{rng.randint(5, 40)}.{rng.randint(0, 99):02d}")
        net = gross - fee
        settlement = BASE_DATE + timedelta(days=rng.randint(0, 20))
        cp = rng.choice(COUNTERPARTIES)
        ledger.append(row(f"L{n:05d}", settlement, gross, "USD", ref, cp))
        bank.append(row(f"B{n:05d}", settlement, net, "USD", ref, cp))
        n += 1

    # Date lag: T+1 or T+2 settlement on the bank side.
    for j in range(3):
        ref = f"INV-{23000 + j}"
        amount = _amount(rng)
        settlement = BASE_DATE + timedelta(days=rng.randint(0, 18))
        lag = rng.choice([1, 2])
        cp = rng.choice(COUNTERPARTIES)
        ledger.append(row(f"L{n:05d}", settlement, amount, "USD", ref, cp))
        bank.append(
            row(f"B{n:05d}", settlement + timedelta(days=lag), amount, "USD", ref, cp)
        )
        n += 1

    # Duplicate: the same ledger line booked twice.
    ref = "INV-24000"
    amount = _amount(rng)
    settlement = BASE_DATE + timedelta(days=5)
    cp = rng.choice(COUNTERPARTIES)
    ledger.append(row(f"L{n:05d}", settlement, amount, "USD", ref, cp))
    bank.append(row(f"B{n:05d}", settlement, amount, "USD", ref, cp))
    n += 1
    ledger.append(row(f"L{n:05d}", settlement, amount, "USD", ref, cp))
    n += 1

    # Currency mismatch: same amount, wrong currency code on the bank side.
    ref = "INV-25000"
    amount = _amount(rng)
    settlement = BASE_DATE + timedelta(days=7)
    cp = rng.choice(COUNTERPARTIES)
    ledger.append(row(f"L{n:05d}", settlement, amount, "USD", ref, cp))
    bank.append(row(f"B{n:05d}", settlement, amount, "EUR", ref, cp))
    n += 1

    # Shuffle so breaks are not all clustered at the end of the files.
    rng.shuffle(ledger)
    rng.shuffle(bank)
    return ledger, bank


FIELDNAMES = [
    "txn_id",
    "settlement_date",
    "amount",
    "currency",
    "reference",
    "counterparty",
]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    ledger, bank = generate()
    write_csv(DATA_DIR / "ledger.csv", ledger)
    write_csv(DATA_DIR / "bank.csv", bank)
    print(f"Wrote {len(ledger)} ledger rows to {DATA_DIR / 'ledger.csv'}")
    print(f"Wrote {len(bank)} bank rows to {DATA_DIR / 'bank.csv'}")


if __name__ == "__main__":
    main()
