"""Loopback-only JSON API for RelayLab."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from relaylab import ConflictError, MutableClock, RelayService, StateError, ValidationError  # noqa: E402

HOST = os.environ.get("RELAYLAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("RELAYLAB_PORT", "8179"))
if HOST not in {"127.0.0.1", "localhost"}:
    raise SystemExit("RelayLab refuses non-loopback binding")

ALLOWED_ORIGINS = {
    "http://127.0.0.1:5179", "http://localhost:5179",
    "http://127.0.0.1:4179", "http://localhost:4179",
}
clock = MutableClock(datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc).timestamp())
database_path = Path(os.environ.get("RELAYLAB_DB", Path(__file__).parent.parent / "relay-lab.sqlite3"))
service = RelayService(database_path, clock.now)
stored_clock = service.stored_clock()
if stored_clock and stored_clock > clock.now():
    clock.restore(stored_clock)
service.persist_clock(clock.now())
service.seed_if_empty()


class Handler(BaseHTTPRequestHandler):
    server_version = "RelayLab/0.1"

    def _json(self, status: int, body: object) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _allow_mutation(self) -> bool:
        origin = self.headers.get("Origin")
        cli = self.headers.get("X-RelayLab-CLI") == "1"
        return origin in ALLOWED_ORIGINS or (origin is None and cli)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 65_536:
            raise ValidationError("request body must be 1–65536 bytes")
        try:
            value = json.loads(
                self.rfile.read(length),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite number {value} is not valid JSON")),
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValidationError("request body must be valid JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("request body must be a JSON object")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        if origin not in ALLOWED_ORIGINS:
            self._json(403, {"error": "origin_not_allowed"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Idempotency-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._json(403, {"error": "loopback_only"})
            return
        if urlparse(self.path).path == "/api/snapshot":
            self._json(200, service.snapshot())
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._json(403, {"error": "loopback_only"})
            return
        if not self._allow_mutation():
            self._json(403, {"error": "exact_local_origin_required"})
            return
        try:
            body = self._body()
            path = urlparse(self.path).path
            if path == "/api/deliveries":
                key = self.headers.get("Idempotency-Key") or body.get("idempotencyKey", "")
                delivery, created = service.create_delivery(key, body.get("receiver", ""), body.get("payload"))
                self._json(201 if created else 200, {"created": created, "delivery": delivery})
                return
            if path == "/api/worker/run":
                delivery = service.run_worker(body.get("worker", "worker-a"))
                self._json(200, {"worked": delivery is not None, "delivery": delivery})
                return
            if path == "/api/clock/advance":
                now = clock.advance(float(body.get("seconds", 10)))
                service.persist_clock(now)
                self._json(200, {"clock": now, "clockMode": "simulated"})
                return
            if path.startswith("/api/deliveries/") and path.endswith("/replay"):
                delivery_id = path.split("/")[3]
                self._json(200, {"delivery": service.replay(delivery_id)})
                return
            self._json(404, {"error": "not_found"})
        except ConflictError as error:
            self._json(409, {"error": "idempotency_conflict", "detail": str(error)})
        except StateError as error:
            self._json(409, {"error": "invalid_state", "detail": str(error)})
        except (ValidationError, ValueError) as error:
            self._json(400, {"error": "invalid_request", "detail": str(error)})
        except Exception:
            self._json(500, {"error": "internal_error", "detail": "unexpected server error"})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[relaylab] {self.address_string()} {format % args}")


if __name__ == "__main__":
    print(f"RelayLab API: http://{HOST}:{PORT} (simulated clock, loopback only)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
