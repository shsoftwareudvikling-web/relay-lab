from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from relaylab import ConflictError, MutableClock, RelayService, StateError, ValidationError, canonical_payload, payload_signature, verify_signature


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value
        self.lock = threading.Lock()

    def now(self) -> float:
        with self.lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


class RelayServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "relay.sqlite3"
        self.clock = FakeClock()
        self.service = RelayService(self.db_path, self.clock.now)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create(self, key: str = "evt-001", receiver: str = "healthy"):
        return self.service.create_delivery(key, receiver, {"event": "order.paid", "id": 42})

    def test_idempotency_returns_existing_and_rejects_conflicting_payload(self) -> None:
        first, created = self.create()
        same, created_again = self.create()
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], same["id"])
        with self.assertRaises(ConflictError):
            self.service.create_delivery("evt-001", "healthy", {"event": "order.failed"})

    def test_invalid_input_types_and_nonfinite_clock_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.create_delivery("evt-list", [], {"event": "bad"})
        with self.assertRaises(ValidationError):
            self.service.run_worker(14)
        mutable = MutableClock(1000)
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValidationError):
                mutable.advance(value)

    def test_concurrent_same_key_creates_once_and_returns_existing(self) -> None:
        barrier = threading.Barrier(3)
        results: list[tuple[str, bool]] = []
        def create() -> None:
            barrier.wait()
            delivery, created = self.create("evt-concurrent")
            results.append((delivery["id"], created))
        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(1, len({item[0] for item in results}))
        self.assertEqual([False, True], sorted(item[1] for item in results))

    def test_persisted_clock_restores_lease_timeline_after_restart(self) -> None:
        delivery, _ = self.create("evt-clock")
        self.service.force_claim_for_test(delivery["id"], "worker-a", 10)
        self.clock.advance(8)
        self.service.persist_clock(self.clock.now())
        restarted_clock = FakeClock(self.service.stored_clock() or 0)
        restarted = RelayService(self.db_path, restarted_clock.now)
        self.assertIsNone(restarted.force_claim_for_test(delivery["id"], "worker-b", 10))
        restarted_clock.advance(3)
        self.assertIsNotNone(restarted.force_claim_for_test(delivery["id"], "worker-b", 10))

    def test_restart_preserves_delivery_and_attempt_history(self) -> None:
        delivery, _ = self.create()
        self.service.run_worker("worker-a")
        restarted = RelayService(self.db_path, self.clock.now)
        restored = restarted.snapshot()["deliveries"][0]
        self.assertEqual(delivery["id"], restored["id"])
        self.assertEqual("delivered", restored["status"])
        self.assertEqual(1, len(restored["attempts"]))

    def test_transient_failure_obeys_backoff_then_delivers(self) -> None:
        delivery, _ = self.create(receiver="transient_failure")
        first = self.service.run_worker("worker-a")
        self.assertEqual("retrying", first["status"])
        self.assertEqual(self.clock.now() + 5, first["nextAttemptAt"])
        self.assertIsNone(self.service.run_worker("worker-b"))
        self.clock.advance(5)
        second = self.service.run_worker("worker-b")
        self.assertEqual(delivery["id"], second["id"])
        self.assertEqual("delivered", second["status"])

    def test_repeated_signature_rejection_reaches_dead_letter(self) -> None:
        self.create(receiver="invalid_signature")
        first = self.service.run_worker()
        self.assertEqual("retrying", first["status"])
        self.clock.advance(5)
        second = self.service.run_worker()
        self.assertEqual("retrying", second["status"])
        self.clock.advance(15)
        third = self.service.run_worker()
        self.assertEqual("dead_letter", third["status"])
        self.assertEqual(["signature_rejected"] * 3, [attempt["outcome"] for attempt in third["attempts"]])

    def test_tampered_payload_is_rejected_by_hmac_verification(self) -> None:
        original = canonical_payload({"event": "account.updated", "id": 9})
        signature = payload_signature(original)
        tampered = canonical_payload({"event": "account.deleted", "id": 9})
        self.assertTrue(verify_signature(original, signature))
        self.assertFalse(verify_signature(tampered, signature))

    def test_expired_lease_can_be_recovered(self) -> None:
        delivery, _ = self.create()
        first_claim = self.service.force_claim_for_test(delivery["id"], "worker-a", 10)
        self.assertIsNotNone(first_claim)
        self.assertIsNone(self.service.force_claim_for_test(delivery["id"], "worker-b", 10))
        self.clock.advance(11)
        recovered = self.service.force_claim_for_test(delivery["id"], "worker-b", 10)
        self.assertEqual("worker-b", recovered["lease_owner"])

    def test_transactional_claim_allows_only_one_concurrent_worker(self) -> None:
        self.create()
        barrier = threading.Barrier(3)
        results: list[object] = []

        def claim(worker: str) -> None:
            barrier.wait()
            results.append(self.service.claim_next(worker))

        threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(1, sum(result is not None for result in results))

    def test_stale_same_named_worker_is_fenced_by_claim_token(self) -> None:
        self.create("evt-fenced")
        original_dispatch = self.service._dispatch
        first_inside = threading.Event()
        release_first = threading.Event()
        invocation_lock = threading.Lock()
        invocations = 0
        errors: list[Exception] = []
        def controlled_dispatch(delivery):
            nonlocal invocations
            with invocation_lock:
                invocations += 1
                position = invocations
            if position == 1:
                first_inside.set()
                release_first.wait(2)
            return original_dispatch(delivery)
        self.service._dispatch = controlled_dispatch
        def stale_run() -> None:
            try:
                self.service.run_worker("same-name")
            except Exception as error:
                errors.append(error)
        stale = threading.Thread(target=stale_run)
        stale.start()
        self.assertTrue(first_inside.wait(1))
        self.clock.advance(11)
        winner = self.service.run_worker("same-name")
        release_first.set()
        stale.join()
        self.assertEqual("delivered", winner["status"])
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], StateError)

    def test_successful_replay_is_receiver_idempotent(self) -> None:
        delivery, _ = self.create()
        delivered = self.service.run_worker()
        self.assertEqual("accepted", delivered["attempts"][0]["outcome"])
        replayed = self.service.replay(delivery["id"])
        self.assertEqual("queued", replayed["status"])
        self.assertEqual(0, replayed["attemptsCount"])
        delivered_again = self.service.run_worker("worker-replay")
        self.assertEqual("delivered", delivered_again["status"])
        self.assertEqual("duplicate_suppressed", delivered_again["attempts"][0]["outcome"])
        self.assertEqual(2, len(delivered_again["attempts"]))


if __name__ == "__main__":
    unittest.main()
