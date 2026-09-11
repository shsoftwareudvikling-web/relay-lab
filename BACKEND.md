# Backend

The backend uses only Python's standard library. `ThreadingHTTPServer` exposes a small JSON API and `sqlite3` persists deliveries, attempts, leases, and receiver receipts.

## Delivery lifecycle

1. `POST /api/deliveries` validates an idempotency key, built-in receiver name, and non-empty JSON object up to 16 KiB.
2. The canonical JSON is hashed and signed with HMAC-SHA256. Reusing the key with the same receiver and payload returns the existing delivery; changing either produces `409`.
3. `POST /api/worker/run` transactionally claims one due row with `BEGIN IMMEDIATE`, records a lease, dispatches to its controlled receiver, and appends an attempt.
4. Failures retry after 5, 15, then 30 simulated seconds. The current three-attempt policy reaches `dead_letter` before the third backoff is used.
5. Expired processing leases are claimable by another worker. Replay clears the current cycle count and due time while retaining prior attempts and the receiver receipt.

The API clock starts at a fixed UTC instant for a new database and advances only through `POST /api/clock/advance`. Its current value is persisted with the queue, so a restart cannot move time behind a stored retry or lease. This is deliberate lab behavior, not production scheduling.

## Endpoints

- `GET /api/snapshot`
- `POST /api/deliveries`
- `POST /api/worker/run`
- `POST /api/clock/advance`
- `POST /api/deliveries/{id}/replay`

This lab does not implement distributed consensus, multiple API replicas, background polling, delivery cancellation, arbitrary destinations, or production secret management.
