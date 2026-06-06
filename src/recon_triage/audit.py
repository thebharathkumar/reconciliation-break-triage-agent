"""Append-only, HMAC-chained audit log.

Every entry stores a hash computed as:

    HMAC(secret, prev_hash + canonical_json(entry_without_hash))

Because each hash folds in the previous hash, the entries form a chain: editing
or removing any line invalidates every hash from that point forward. The chain
is what makes the resolution trail defensible. verify_chain walks the file and
returns the index of the first broken link, or None if the chain is intact.

Three event types are logged: break_detected, ai_recommendation, and
human_decision.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# The genesis hash that seeds the very first entry. A fixed, well-known value.
GENESIS_HASH = "0" * 64

EVENT_BREAK_DETECTED = "break_detected"
EVENT_AI_RECOMMENDATION = "ai_recommendation"
EVENT_HUMAN_DECISION = "human_decision"


def _canonical_json(obj: Any) -> str:
    """Serialize deterministically: sorted keys, no incidental whitespace."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hash(secret: bytes, prev_hash: str, entry_without_hash: dict) -> str:
    payload = prev_hash + _canonical_json(entry_without_hash)
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


class AuditLog:
    """A thread-safe, file-backed, HMAC-chained append-only log."""

    def __init__(self, path: str | Path, secret: Optional[str] = None) -> None:
        self.path = Path(path)
        resolved_secret = secret or os.environ.get(
            "AUDIT_SECRET", "dev-insecure-audit-secret"
        )
        self._secret = resolved_secret.encode("utf-8")
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS_HASH
        last = GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    last = json.loads(line)["hash"]
        return last

    def append(self, event_type: str, payload: dict) -> dict:
        """Append an entry and return it (including its computed hash)."""

        with self._lock:
            prev_hash = self._last_hash()
            seq = self._next_seq()
            entry_without_hash = {
                "seq": seq,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "payload": payload,
                "prev_hash": prev_hash,
            }
            entry_hash = _compute_hash(self._secret, prev_hash, entry_without_hash)
            entry = {**entry_without_hash, "hash": entry_hash}
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical_json(entry) + "\n")
            return entry

    def _next_seq(self) -> int:
        if not self.path.exists():
            return 0
        count = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def verify_chain(self) -> Optional[int]:
        """Walk the chain. Return the first broken index, or None if intact."""

        return verify_chain(self.path, self._secret)


def verify_chain(path: str | Path, secret: bytes | str) -> Optional[int]:
    """Verify an audit file independently of any live AuditLog instance.

    Returns the zero-based index of the first entry whose hash or chain linkage
    does not check out, or None if every entry verifies.
    """

    path = Path(path)
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not path.exists():
        return None

    prev_hash = GENESIS_HASH
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return index

            stored_hash = entry.get("hash")
            entry_without_hash = {
                k: v for k, v in entry.items() if k != "hash"
            }

            # The entry must point at the previous entry's hash.
            if entry_without_hash.get("prev_hash") != prev_hash:
                return index

            recomputed = _compute_hash(secret, prev_hash, entry_without_hash)
            if not stored_hash or not hmac.compare_digest(recomputed, stored_hash):
                return index

            prev_hash = stored_hash

    return None
