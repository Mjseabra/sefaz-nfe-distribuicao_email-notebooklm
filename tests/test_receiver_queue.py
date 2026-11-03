import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notebooklm_receiver as receiver


class QueuePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.queue_path = Path(self.temp_dir.name) / "fila_pendencias.json"
        self.catalog_path = Path(self.temp_dir.name) / "catalog.json"
        self.path_patch = patch.object(receiver, "QUEUE_PATH", str(self.queue_path))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.catalog_patch = patch.object(receiver, "CATALOG_PATH", str(self.catalog_path))
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)
        self.lock_patch = patch.object(receiver, "QUEUE_LOCK", threading.RLock())
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_queue_starts_empty(self):
        self.assertEqual(
            receiver._read_queue_state(),
            {"schema_version": 1, "pending": [], "approved": [], "resolved": {}},
        )
        self.assertFalse(self.queue_path.exists())

    def test_json_round_trip_uses_atomic_writer(self):
        state = {
            "schema_version": 1,
            "pending": [{"batch_id": "batch-1", "items": []}],
            "approved": [],
            "resolved": {},
        }

        receiver._write_queue_state(state)

        self.assertEqual(receiver._read_queue_state(), state)
        self.assertFalse(list(self.queue_path.parent.glob("*.tmp")))

    def test_pending_batch_older_than_30_days_survives_reload_in_isolated_json(self):
        created_at = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat().replace("+00:00", "Z")
        batch = {
            "batch_id": "retention-30-days",
            "created_at": created_at,
            "status": "pending",
            "items": [
                {
                    "title": "Retained NFe Document",
                    "date": "2026-09-01",
                    "url": "https://example.invalid/nfe/retained",
                    "file_path": "/isolated/pdf-cache/retained.pdf",
                    "document_id": "retained-document-1",
                }
            ],
        }
        receiver._write_queue_state(
            {
                "schema_version": 1,
                "pending": [batch],
                "approved": [],
                "resolved": {},
            }
        )

        # A fresh read models daemon startup loading the durable queue file.
        reloaded_state = receiver._read_queue_state()

        self.assertEqual(reloaded_state["pending"], [batch])
        self.assertEqual(reloaded_state["pending"][0]["created_at"], created_at)
        self.assertTrue(self.queue_path.is_file())
        self.assertNotEqual(self.queue_path.resolve(), self.catalog_path.resolve())
        self.assertFalse(self.catalog_path.exists())

    def test_malformed_state_fails_closed_without_replacing_file(self):
        malformed = '{"schema_version": 1,'
        self.queue_path.write_text(malformed, encoding="utf-8")

        with self.assertRaises(receiver.QueueStateError):
            receiver._read_queue_state()

        self.assertEqual(self.queue_path.read_text(encoding="utf-8"), malformed)

    def test_concurrent_queue_updates_are_serialized(self):
        receiver._write_queue_state(
            {"schema_version": 1, "pending": [], "approved": [], "resolved": {}, "count": 0}
        )
        errors = []

        def increment():
            try:
                with receiver.QUEUE_LOCK:
                    state = receiver._read_queue_state()
                    state["count"] += 1
                    receiver._write_queue_state(state)
            except Exception as error:  # surfaced in the main test thread below
                errors.append(error)

        threads = [threading.Thread(target=increment) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(receiver._read_queue_state()["count"], 20)

    def test_staged_rejection_recovers_catalog_before_removal_idempotently(self):
        state = receiver._empty_queue_state()
        state["pending"].append(
            {
                "batch_id": "reject-1",
                "status": "rejecting",
                "items": [{"title": "Rejected Notice", "document_id": "rejected-1"}],
            }
        )
        receiver._write_queue_state(state)

        receiver.recover_queue_state()
        receiver.recover_queue_state()

        recovered = receiver._read_queue_state()
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(recovered["pending"], [])
        self.assertEqual(recovered["resolved"]["reject-1"]["status"], "rejected")
        self.assertEqual(len(catalog["sources"]), 1)

    def test_staged_completion_recovers_catalog_before_removal_idempotently(self):
        state = receiver._empty_queue_state()
        state["approved"].append(
            {
                "batch_id": "complete-1",
                "status": "completing",
                "items": [{"title": "Processed Notice", "document_id": "processed-1"}],
            }
        )
        receiver._write_queue_state(state)

        receiver.recover_queue_state()
        receiver.recover_queue_state()

        recovered = receiver._read_queue_state()
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(recovered["approved"], [])
        self.assertEqual(recovered["resolved"]["complete-1"]["status"], "complete")
        self.assertEqual(len(catalog["sources"]), 1)


if __name__ == "__main__":
    unittest.main()
