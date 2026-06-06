# Pitch: Reconciliation Break Triage Agent

A drop-in narrative for a README hero section, a demo intro, or a one-page brief.

## The problem, in one breath

Every clearing and settlement operation runs a daily reconciliation: the internal
payments ledger against the bank or nostro statement. When the two disagree, the
gap is a "break." Each break has to be explained, assigned a cause, and resolved
by an operator, and the whole resolution trail has to stand up to a regulator who
may ask, months later, "who decided this, on what basis, and has the record been
touched since?" Most teams do this in spreadsheets and email. The reasoning lives
in someone's head, the audit trail is a folder of attachments, and "has anyone
edited this?" has no good answer.

## What this is

A small, auditable system that does the full loop:

- **Detects and classifies** every break with deterministic logic (no model in
  the matching path), into six types: missing on one side, amount mismatch (a
  fee deduction), date lag (T+1 or T+2 settlement), duplicate, or currency
  mismatch.
- **Explains** each break with a Claude agent that returns a structured
  recommendation, grounded only in the break data and a documented resolution
  policy. It references the specific fields it reasoned over and never invents a
  transaction.
- **Waits for a human.** The operator sees the AI call and approves, rejects, or
  corrects it. Nothing resolves on its own.
- **Proves the trail.** Every detection, recommendation, and decision is appended
  to an HMAC-chained log. Change one byte of any past entry and the chain breaks
  at exactly that point, which a one-command demo shows live.

## Why it lands

The two things a reconciliation tool has to earn are **trust** and
**defensibility**, and this system makes both legible:

- **Trust comes from the human gate, not the model.** The agent advises; a named
  operator decides. The model is a fast, grounded explainer, not an autonomous
  actor over the firm's books.
- **Defensibility comes from cryptography, not promises.** The audit log is
  tamper-evident by construction. "The record is unaltered" stops being a claim
  and becomes something you can verify in front of an auditor in ten seconds.

## The ten-second demo

```bash
python scripts/verify_audit.py --demo
```

It builds a chained log, verifies it, edits a single recommendation, and verifies
again to show the chain break. That is the whole auditability thesis, runnable on
a laptop, with no API key required.

## What is deliberately small

No database, no auth, no queue. The matcher is pure logic and fully tested; the
agent is one grounded call per break with a rule-based fallback so the demo runs
offline; the audit log is an append-only file. It is meant to be read end to end
and to make one point well: explainable, human-approved, tamper-evident break
resolution.
