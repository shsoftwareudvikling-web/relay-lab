# Testing

Run the narrow verification suite from this directory:

```powershell
pnpm test
pnpm typecheck
pnpm build
```

The backend tests use temporary SQLite databases and an injected deterministic clock. They cover atomic concurrent idempotency, conflicting payloads, restart persistence, persisted simulated time, transient retry timing, dead-letter exhaustion, HMAC tamper rejection, expired lease recovery, concurrent single-worker claim, claim-token fencing for a stale worker, and successful replay with duplicate suppression at the receiver. HTTP boundary tests confirm malformed receiver, worker, and non-finite numeric inputs return structured `400` responses.

The TypeScript check validates API data handling and interface state. The Vite production build verifies the standalone browser bundle. Manual browser QA should cover selecting/filtering rows, each control receiver, advancing the clock until a retry becomes due, replaying a delivery, keyboard focus, narrow layout, and an unavailable API state.
