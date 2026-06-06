"""Reconciliation break triage agent.

A small, auditable system that reconciles an internal payments ledger against a
bank or nostro statement, classifies the mismatches (breaks), has an LLM agent
explain and propose resolutions, and requires a human to approve before
anything is marked resolved. Every step is written to a tamper-evident audit
log.
"""

__version__ = "0.1.0"
