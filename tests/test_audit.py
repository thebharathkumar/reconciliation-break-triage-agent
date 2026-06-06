"""Tests for the HMAC-chained audit log: integrity and tamper detection."""

from __future__ import annotations

import json

from recon_triage.audit import (
    EVENT_AI_RECOMMENDATION,
    EVENT_BREAK_DETECTED,
    EVENT_HUMAN_DECISION,
    AuditLog,
    verify_chain,
)


def _make_log(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", secret="test-secret")
    log.append(EVENT_BREAK_DETECTED, {"break_id": "BRK-0001", "type": "DATE_LAG"})
    log.append(EVENT_AI_RECOMMENDATION, {"break_id": "BRK-0001", "action": "await_settlement"})
    log.append(EVENT_HUMAN_DECISION, {"break_id": "BRK-0001", "actor": "ops", "decision": "approve"})
    return log


def test_chain_verifies_when_intact(tmp_path):
    log = _make_log(tmp_path)
    assert log.verify_chain() is None
    assert len(log.entries()) == 3


def test_each_entry_links_to_previous(tmp_path):
    log = _make_log(tmp_path)
    entries = log.entries()
    assert entries[0]["prev_hash"] == "0" * 64
    assert entries[1]["prev_hash"] == entries[0]["hash"]
    assert entries[2]["prev_hash"] == entries[1]["hash"]


def test_sequence_numbers_increment(tmp_path):
    log = _make_log(tmp_path)
    seqs = [e["seq"] for e in log.entries()]
    assert seqs == [0, 1, 2]


def test_tampering_with_payload_breaks_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    _make_log(tmp_path)

    lines = path.read_text().splitlines()
    entry = json.loads(lines[1])
    # Quietly change the recommended action; leave the stored hash untouched.
    entry["payload"]["action"] = "write_off"
    lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    assert verify_chain(path, "test-secret") == 1


def test_tampering_with_hash_breaks_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    _make_log(tmp_path)

    lines = path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["hash"] = "f" * 64
    lines[0] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    # The forged first hash fails, and it also breaks the link for entry 1.
    assert verify_chain(path, "test-secret") == 0


def test_deleting_an_entry_breaks_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    _make_log(tmp_path)

    lines = path.read_text().splitlines()
    # Remove the middle entry; entry 2 now points at a hash that is gone.
    del lines[1]
    path.write_text("\n".join(lines) + "\n")

    assert verify_chain(path, "test-secret") == 1


def test_wrong_secret_fails_verification(tmp_path):
    path = tmp_path / "audit.jsonl"
    _make_log(tmp_path)
    # A verifier without the right secret cannot validate any entry.
    assert verify_chain(path, "wrong-secret") == 0


def test_empty_log_is_intact(tmp_path):
    log = AuditLog(tmp_path / "empty.jsonl", secret="test-secret")
    assert log.verify_chain() is None
