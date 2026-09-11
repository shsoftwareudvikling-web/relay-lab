# Changelog

## 2026-09-11

- Added the standalone dependency lockfile and clarified the bounded delivery model before public publication.

- Added the standalone RelayLab React and TypeScript operational console.
- Added the loopback-only Python standard-library API and persisted SQLite outbox.
- Implemented idempotency conflict detection, HMAC envelope verification, controlled receiver faults, retry backoff, dead-lettering, replay, transactional worker leases, expiry recovery, and receiver-side duplicate suppression.
- Added atomic concurrent delivery creation, persisted simulated time, and unique lease-token fencing for stale same-named workers.
- Added deterministic domain tests, local launcher, responsive and reduced-motion behavior, and implementation/security documentation.
