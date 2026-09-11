"""RelayLab's SQLite outbox engine. Python standard library only."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEMO_HMAC_KEY = b"relaylab-local-demo-key"
RECEIVERS = {"healthy", "transient_failure", "invalid_signature"}
BACKOFF_SECONDS = (5, 15, 30)


class ConflictError(ValueError):
    pass


class ValidationError(ValueError):
    pass


class StateError(ValueError):
    pass


@dataclass
class MutableClock:
    value: float

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> float:
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0 or seconds > 3600:
            raise ValidationError("advance must be between 1 and 3600 seconds")
        with self._lock:
            self.value += seconds
            return self.value

    def restore(self, value: float) -> float:
        with self._lock:
            if value > self.value:
                self.value = value
            return self.value


def canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_signature(payload_text: str) -> str:
    return hmac.new(DEMO_HMAC_KEY, payload_text.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(payload_text: str, signature: str) -> bool:
    return hmac.compare_digest(payload_signature(payload_text), signature)


class RelayService:
    def __init__(self, db_path: str | Path, clock: Callable[[], float]) -> None:
        self.db_path = str(db_path)
        self.clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    receiver TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_attempt_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
                    attempt_no INTEGER NOT NULL,
                    worker TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL,
                    outcome TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receiver_receipts (
                    delivery_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    received_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_delivery_claim
                    ON deliveries(status, next_attempt_at, lease_expires_at);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(deliveries)")}
            if "lease_token" not in columns:
                connection.execute("ALTER TABLE deliveries ADD COLUMN lease_token TEXT")

    @staticmethod
    def _validate(key: str, receiver: str, payload: Any) -> dict[str, Any]:
        if not isinstance(key, str) or not 3 <= len(key) <= 80 or not all(char.isalnum() or char in "-_." for char in key):
            raise ValidationError("idempotency key must be 3–80 URL-safe characters")
        if not isinstance(receiver, str) or receiver not in RECEIVERS:
            raise ValidationError("receiver must be a built-in controlled receiver")
        if not isinstance(payload, dict) or not payload:
            raise ValidationError("payload must be a non-empty JSON object")
        if len(canonical_payload(payload).encode("utf-8")) > 16_384:
            raise ValidationError("payload exceeds 16 KiB")
        return payload

    def create_delivery(self, key: str, receiver: str, payload: Any) -> tuple[dict[str, Any], bool]:
        payload = self._validate(key, receiver, payload)
        payload_text = canonical_payload(payload)
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        now = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM deliveries WHERE idempotency_key = ?", (key,)).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash or existing["receiver"] != receiver:
                    connection.rollback()
                    raise ConflictError("idempotency key already belongs to a different payload or receiver")
                result = self._delivery_dict(connection, existing)
                connection.commit()
                return result, False
            delivery_id = f"dlv_{uuid.uuid4().hex[:10]}"
            connection.execute(
                """INSERT INTO deliveries
                (id, idempotency_key, receiver, payload, payload_hash, signature, status,
                 attempts_count, max_attempts, next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, 3, ?, ?, ?)""",
                (delivery_id, key, receiver, payload_text, payload_hash, payload_signature(payload_text), now, now, now),
            )
            row = connection.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
            result = self._delivery_dict(connection, row)
            connection.commit()
            return result, True

    def stored_clock(self) -> float | None:
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='simulated_clock'").fetchone()
            return float(row["value"]) if row else None

    def persist_clock(self, value: float) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('simulated_clock', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(value),),
            )

    def seed_if_empty(self) -> None:
        with self._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        if count:
            return
        fixtures = (
            ("fixture-checkout-001", "healthy", {"event": "order.paid", "orderId": "ord_2048", "amount": 64900, "currency": "DKK"}),
            ("fixture-profile-002", "transient_failure", {"event": "profile.updated", "userId": "usr_88", "fields": ["email"]}),
            ("fixture-audit-003", "invalid_signature", {"event": "audit.ready", "reportId": "rep_72h", "severity": "high"}),
        )
        for key, receiver, payload in fixtures:
            self.create_delivery(key, receiver, payload)

    def _delivery_dict(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        attempts = connection.execute(
            "SELECT attempt_no, worker, started_at, finished_at, outcome, http_status, detail FROM attempts WHERE delivery_id = ? ORDER BY id DESC",
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "idempotencyKey": row["idempotency_key"],
            "receiver": row["receiver"],
            "payload": json.loads(row["payload"]),
            "payloadText": row["payload"],
            "payloadHash": row["payload_hash"],
            "signature": row["signature"],
            "signatureValid": verify_signature(row["payload"], row["signature"]),
            "status": row["status"],
            "attemptsCount": row["attempts_count"],
            "maxAttempts": row["max_attempts"],
            "nextAttemptAt": row["next_attempt_at"],
            "leaseOwner": row["lease_owner"],
            "leaseExpiresAt": row["lease_expires_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "attempts": [dict(attempt) for attempt in attempts],
        }

    def snapshot(self) -> dict[str, Any]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM deliveries ORDER BY created_at DESC, id DESC").fetchall()
            deliveries = [self._delivery_dict(connection, row) for row in rows]
        counts = {status: 0 for status in ("queued", "processing", "retrying", "delivered", "dead_letter")}
        for delivery in deliveries:
            counts[delivery["status"]] = counts.get(delivery["status"], 0) + 1
        return {
            "clock": self.clock(),
            "clockMode": "simulated",
            "backoffSeconds": list(BACKOFF_SECONDS),
            "deliverySemantics": "at-least-once; receiver receipt makes accepted delivery idempotent",
            "counts": counts,
            "deliveries": deliveries,
        }

    def claim_next(self, worker: str, lease_seconds: float = 10, delivery_id: str | None = None) -> dict[str, Any] | None:
        now = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            clauses = "((status IN ('queued','retrying') AND next_attempt_at <= ?) OR (status = 'processing' AND lease_expires_at <= ?))"
            params: list[Any] = [now, now]
            if delivery_id:
                clauses += " AND id = ?"
                params.append(delivery_id)
            row = connection.execute(
                f"SELECT * FROM deliveries WHERE {clauses} ORDER BY next_attempt_at, created_at LIMIT 1",
                params,
            ).fetchone()
            if not row:
                connection.commit()
                return None
            updated = connection.execute(
                """UPDATE deliveries SET status='processing', lease_owner=?, lease_token=?, lease_expires_at=?, updated_at=?
                WHERE id=? AND ((status IN ('queued','retrying') AND next_attempt_at <= ?) OR (status='processing' AND lease_expires_at <= ?))""",
                (worker, uuid.uuid4().hex, now + lease_seconds, now, row["id"], now, now),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            claimed = connection.execute("SELECT * FROM deliveries WHERE id = ?", (row["id"],)).fetchone()
            connection.commit()
            return dict(claimed)

    def _dispatch(self, delivery: dict[str, Any]) -> tuple[bool, int, str, str]:
        payload_text = delivery["payload"]
        receiver = delivery["receiver"]
        signature = delivery["signature"]
        if receiver == "invalid_signature":
            payload_text = payload_text[:-1] + ',"tampered":true}'
        if not verify_signature(payload_text, signature):
            return False, 401, "signature_rejected", "receiver rejected the modified payload signature"
        if receiver == "transient_failure" and delivery["attempts_count"] == 0:
            return False, 503, "transient_failure", "controlled receiver unavailable on first attempt"
        with self._connection() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO receiver_receipts(delivery_id, payload_hash, received_at) VALUES (?, ?, ?)",
                (delivery["id"], delivery["payload_hash"], self.clock()),
            )
            receipt = connection.execute("SELECT payload_hash FROM receiver_receipts WHERE delivery_id = ?", (delivery["id"],)).fetchone()
            if receipt["payload_hash"] != delivery["payload_hash"]:
                return False, 409, "receiver_conflict", "receiver already accepted this id with another payload"
            if inserted.rowcount == 0:
                return True, 200, "duplicate_suppressed", "receiver returned its existing receipt"
        return True, 202, "accepted", "receiver verified signature and stored one receipt"

    def run_worker(self, worker: str = "worker-a") -> dict[str, Any] | None:
        if not isinstance(worker, str) or not worker or len(worker) > 40:
            raise ValidationError("worker name is required and limited to 40 characters")
        claimed = self.claim_next(worker)
        if not claimed:
            return None
        started = self.clock()
        success, http_status, outcome, detail = self._dispatch(claimed)
        finished = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM deliveries WHERE id = ?", (claimed["id"],)).fetchone()
            if not current or current["status"] != "processing" or current["lease_token"] != claimed["lease_token"]:
                connection.rollback()
                raise StateError("worker no longer owns the delivery lease")
            attempt_no = connection.execute("SELECT COUNT(*) FROM attempts WHERE delivery_id = ?", (claimed["id"],)).fetchone()[0] + 1
            cycle_attempt = current["attempts_count"] + 1
            if success:
                status, next_at = "delivered", finished
            elif cycle_attempt >= current["max_attempts"]:
                status, next_at = "dead_letter", finished
            else:
                status = "retrying"
                next_at = finished + BACKOFF_SECONDS[min(cycle_attempt - 1, len(BACKOFF_SECONDS) - 1)]
            connection.execute(
                "INSERT INTO attempts(delivery_id, attempt_no, worker, started_at, finished_at, outcome, http_status, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (claimed["id"], attempt_no, worker, started, finished, outcome, http_status, detail),
            )
            connection.execute(
                """UPDATE deliveries SET status=?, attempts_count=?, next_attempt_at=?, lease_owner=NULL, lease_token=NULL,
                lease_expires_at=NULL, updated_at=? WHERE id=?""",
                (status, cycle_attempt, next_at, finished, claimed["id"]),
            )
            row = connection.execute("SELECT * FROM deliveries WHERE id = ?", (claimed["id"],)).fetchone()
            connection.commit()
            return self._delivery_dict(connection, row)

    def replay(self, delivery_id: str) -> dict[str, Any]:
        now = self.clock()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
            if not row:
                connection.rollback()
                raise ValidationError("delivery not found")
            if row["status"] not in {"delivered", "dead_letter"}:
                connection.rollback()
                raise StateError("only delivered or dead-letter deliveries can be replayed")
            connection.execute(
                """UPDATE deliveries SET status='queued', attempts_count=0, next_attempt_at=?,
                lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?""",
                (now, now, delivery_id),
            )
            updated = connection.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
            connection.commit()
            return self._delivery_dict(connection, updated)

    def force_claim_for_test(self, delivery_id: str, worker: str, lease_seconds: float) -> dict[str, Any] | None:
        return self.claim_next(worker, lease_seconds=lease_seconds, delivery_id=delivery_id)
