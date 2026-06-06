# Reconciliation Resolution Policy

This policy governs how clearing operations resolves breaks between the internal
payments ledger and the bank or nostro statement. The triage agent grounds its
recommendations in these procedures and cites the relevant section. Every
resolution requires human approval before a break is marked resolved.

## Policy 1: Missing in bank

A payment is present in the ledger with no matching bank line. If the ledger
settlement_date is inside the settlement window, the bank credit is likely still
in flight. Recommended action: await_settlement. Escalate only if the entry has
aged beyond the expected window.

## Policy 2: Missing in ledger

The bank reports a movement with no matching ledger entry. This usually means an
instruction was not booked or was booked under a different reference.
Recommended action: manual_review to locate the originating instruction and book
the ledger entry. Escalate if no instruction can be found.

## Policy 3: Amount mismatch

Reference and currency agree but the amounts differ by a small, fee-sized
margin. This is consistent with a deducted bank charge, intermediary fee, or FX
spread. Recommended action: post_adjustment for the difference and reconcile the
net. Investigate further if the difference exceeds the fee tolerance.

## Policy 4: Date lag

Amount and currency agree but settlement_date differs by one or two days,
consistent with T+1 or T+2 settlement. No financial adjustment is required.
Recommended action: await_settlement on the lagged side.

## Policy 5: Duplicate

The same line (reference, amount, currency, settlement_date) appears more than
once on one book. Recommended action: write_off the duplicate by reversing the
extra entry and retaining the original.

## Policy 6: Currency mismatch

Reference and amount agree but the currency code differs. This may indicate a
booking error or an unrecorded FX conversion. Recommended action: escalate to FX
operations to confirm the correct settlement currency before any adjustment.

## Allowed actions

- post_adjustment: book a correcting entry for a known difference.
- await_settlement: no action now; expect the matching line to arrive.
- escalate: route to a specialist team (FX, aged items, exceptions).
- write_off: reverse or remove an entry that should not stand.
- manual_review: a human must investigate before any booking.
