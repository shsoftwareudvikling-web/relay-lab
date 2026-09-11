# Security boundaries

RelayLab is intentionally local and controlled.

- The server refuses any bind host except `127.0.0.1` or `localhost`.
- Mutating browser requests require an exact allowlisted local origin on port 5179 or preview port 4179. A no-origin command-line request additionally needs `X-RelayLab-CLI: 1`.
- Receiver choice is an allowlist; no input can supply a URL, hostname, or outbound request target.
- Payloads must be non-empty strict JSON objects no larger than 16 KiB, non-finite numbers are rejected, request bodies are capped at 64 KiB, and idempotency keys use a limited character set.
- Signatures use HMAC-SHA256 with constant-time comparison.
- SQLite claims use an immediate transaction plus an expiring owner lease.

The checked-in HMAC value is explicitly a demo key. It protects the integrity exercise, not a real secret. The application has no authentication, TLS, key rotation, rate limiting, process isolation, or sandbox for payload content and must not be exposed to a network or used for real webhook data.
