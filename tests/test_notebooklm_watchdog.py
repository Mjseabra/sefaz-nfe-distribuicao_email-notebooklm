import importlib.util
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "watchdog", Path(__file__).resolve().parents[1] / "ops" / "nfe_notebooklm_watchdog.py"
)
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


def held_batch(**overrides):
    batch = {
        "batch_id": "b1",
        "delivery_status": "needs_reconciliation",
        "items": [
            {"title": "A", "delivery": {"email": "succeeded", "google_drive": "succeeded", "notebooklm": "uncertain"}},
            {"title": "B", "delivery": {"email": "succeeded", "google_drive": "succeeded", "notebooklm": "uncertain"}},
        ],
        "delivery_failures": [{"items": [{"title": "A", "drive_file_id": "dA"}, {"title": "B", "drive_file_id": "dB"}]}],
    }
    batch.update(overrides)
    return batch


class ReconcileTests(unittest.TestCase):
    def run_reconcile(self, batch, present=()):
        calls = []
        cli_calls = []

        def fake_daemon(path, token, payload=None):
            calls.append((path, token, payload))
            return {"batches": [batch]}

        def fake_run(args):
            cli_calls.append(args)
            return mock.Mock(returncode=0, stdout="")

        with mock.patch.object(watchdog, "daemon", fake_daemon), mock.patch.object(
            watchdog, "run", fake_run
        ), mock.patch.object(watchdog, "existing_drive_ids", lambda: set(present)), mock.patch.object(
            watchdog, "telegram"
        ) as telegram:
            watchdog.reconcile("fluxb", "oper")
        return calls, cli_calls, telegram

    def test_held_batch_is_linked_and_completed(self):
        calls, cli_calls, telegram = self.run_reconcile(held_batch())
        self.assertEqual(len(cli_calls), 2)
        self.assertEqual([c[2]["action"] for c in calls[1:]], ["record_step", "complete"])
        self.assertEqual(calls[1][1], "oper")
        self.assertEqual(calls[2][1], "fluxb")
        telegram.assert_called_once()

    def test_sources_already_in_notebook_are_not_added_again(self):
        calls, cli_calls, _ = self.run_reconcile(held_batch(), present={"dA", "dB"})
        self.assertEqual(cli_calls, [])
        self.assertEqual(len(calls), 3)

    def test_batch_without_drive_ids_is_left_alone(self):
        calls, cli_calls, _ = self.run_reconcile(held_batch(delivery_failures=[]))
        self.assertEqual(len(calls), 1)
        self.assertEqual(cli_calls, [])

    def test_batch_with_unfinished_email_is_left_alone(self):
        batch = held_batch()
        batch["items"][0]["delivery"]["email"] = "uncertain"
        calls, cli_calls, _ = self.run_reconcile(batch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cli_calls, [])


if __name__ == "__main__":
    unittest.main()
