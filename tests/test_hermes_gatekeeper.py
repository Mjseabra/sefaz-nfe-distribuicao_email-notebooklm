import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from hermes.nfe_gatekeeper import (
    DEFAULT_ENV_FILE,
    GatekeeperError,
    decide_message,
    format_approval_message,
    format_failure_message,
    main,
    poll_once,
    request_json,
    send_telegram,
    test_message,
    validate_internal_url,
)


class GatekeeperMessageTests(unittest.TestCase):
    def test_grouped_message_contains_every_complete_title_and_date(self):
        batch = {
            "batch_id": "batch-001",
            "items": [
                {"title": "SEFAZ Schema Alpha - Publicacao 1", "date": "2026-10-30"},
                {"title": "SEFAZ Schema Beta - Publicacao 2", "date": "2026-10-31"},
            ],
        }

        message = format_approval_message(batch)

        for item in batch["items"]:
            self.assertIn(item["title"], message)
            self.assertIn(item["date"], message)
        self.assertIn(batch["batch_id"], message)
        self.assertIn("Aprovar ou Rejeitar", message)

    def test_test_message_is_explicitly_non_actionable_and_contains_all_items(self):
        message = test_message()

        self.assertIn("TESTE DE DISPARO", message)
        self.assertIn("NAO RESPONDA", message)
        self.assertIn("Nenhuma distribuicao sera executada", message)
        self.assertNotIn("Aprovar", message)
        self.assertNotIn("Rejeitar", message)
        self.assertIn("DOCUMENTO SEFAZ SIMULADO A - HOMOLOGACAO", message)
        self.assertIn("2026-10-30", message)
        self.assertIn("DOCUMENTO SEFAZ SIMULADO B - HOMOLOGACAO", message)
        self.assertIn("2026-10-31", message)

    def test_telegram_send_accepts_hermes_success_acknowledgement(self):
        response = MagicMock(returncode=0, stdout='{"success": true}', stderr="")
        with patch("hermes.nfe_gatekeeper.subprocess.run", return_value=response):
            acknowledgement = send_telegram("synthetic message")
        self.assertTrue(acknowledgement["success"])

    def test_telegram_send_rejects_skipped_acknowledgement(self):
        response = MagicMock(
            returncode=0,
            stdout='{"success": true, "skipped": true}',
            stderr="",
        )
        with patch("hermes.nfe_gatekeeper.subprocess.run", return_value=response):
            with self.assertRaises(GatekeeperError):
                send_telegram("synthetic message")

    def test_internal_service_urls_allow_only_expected_docker_hosts_or_private_ips(self):
        self.assertEqual(validate_internal_url("http://n8n:5678/"), "http://n8n:5678")
        self.assertEqual(
            validate_internal_url("http://172.19.0.1:8555"),
            "http://172.19.0.1:8555",
        )
        for url in (
            "https://n8n:5678",
            "http://localhost:8555",
            "http://example.com",
            "http://n8n:5678/webhook",
        ):
            with self.subTest(url=url), self.assertRaises(GatekeeperError):
                validate_internal_url(url)

    def test_internal_requests_use_bearer_auth_and_require_json(self):
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Type": "application/json"}
        response.read.return_value = b'{"ok": true}'
        response.__enter__.return_value = response
        with patch("hermes.nfe_gatekeeper.urlopen", return_value=response) as urlopen:
            result = request_json("http://n8n:5678/internal", "unit-test-token")
        self.assertEqual(result, {"ok": True})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer unit-test-token")

class GatekeeperOperationTests(unittest.TestCase):
    def test_decide_message_ignores_invalid_text_and_unauthorized_sender(self):
        invalid_messages = (
            (123456789, "batch-001", "talvez"),
            (123456789, "another-batch", "Aprovar"),
            (719001696, "batch-001", "Aprovar"),
        )
        with patch("hermes.nfe_gatekeeper.request_json") as request:
            for chat_id, reply_to_batch_id, text in invalid_messages:
                with self.subTest(chat_id=chat_id, text=text):
                    with self.assertRaises(GatekeeperError):
                        decide_message(
                            "batch-001",
                            chat_id,
                            reply_to_batch_id,
                            text,
                        )
        request.assert_not_called()

    def test_decide_message_normalizes_only_exact_approved_replies(self):
        with patch(
            "hermes.nfe_gatekeeper._resolve_validated_decision",
            return_value={"status": "approved"},
        ) as decide_call:
            result = decide_message(
                "batch-001",
                123456789,
                "batch-001",
                "  APRÓVÁR  ",
            )
        self.assertEqual(result["status"], "approved")
        decide_call.assert_called_once_with(
            "batch-001",
            "approve",
            env_file=DEFAULT_ENV_FILE,
        )

    def test_ambiguous_cli_reply_is_rejected_before_daemon_mutation(self):
        with patch("hermes.nfe_gatekeeper.request_json") as request:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = main(
                    [
                        "decide-message",
                        "batch-001",
                        "--chat-id",
                        "123456789",
                        "--reply-to-batch-id",
                        "batch-001",
                        "--text",
                        "Talvez",
                    ]
                )
        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(stderr.getvalue())["error"],
            "Decision text must be Aprovar or Rejeitar",
        )
        request.assert_not_called()

    def test_action_only_decision_command_is_not_exposed(self):
        with patch("hermes.nfe_gatekeeper.request_json") as request:
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(["decide", "batch-001", "approve"])
        self.assertEqual(error.exception.code, 2)
        request.assert_not_called()

    def test_poll_notifies_only_once_per_pending_batch_and_resets_when_empty(self):
        settings = {
            "daemon_url": "http://172.19.0.1:8555",
            "queue_token": "do-not-log-this",
            "n8n_url": "http://n8n:5678",
            "webhook_token": "do-not-log-this-either",
            "trigger_enabled": True,
        }
        batch = {
            "batch_id": "batch-001",
            "items": [
                {"title": "Complete title Alpha", "date": "2026-10-30"},
                {"title": "Complete title Beta", "date": "2026-10-31"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            with (
                patch("hermes.nfe_gatekeeper.load_settings", return_value=settings),
                patch("hermes.nfe_gatekeeper.pending_failure_notifications", return_value=[]),
                patch("hermes.nfe_gatekeeper.pending_batch", return_value=batch),
                patch("hermes.nfe_gatekeeper.send_telegram") as send,
            ):
                first = poll_once(state_file=state_path)
                second = poll_once(state_file=state_path)
                self.assertEqual(first["status"], "notified")
                self.assertEqual(second["status"], "already_notified")
                send.assert_called_once()
                generated_message = send.call_args.args[0]
                for item in batch["items"]:
                    self.assertIn(item["title"], generated_message)
                    self.assertIn(item["date"], generated_message)
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["last_notified_batch_id"], "batch-001")

                with patch("hermes.nfe_gatekeeper.pending_batch", return_value=None):
                    empty = poll_once(state_file=state_path)
                self.assertEqual(empty["status"], "empty")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertIsNone(state["last_notified_batch_id"])

    def test_poll_sends_notebooklm_failure_to_telegram_then_acknowledges_it(self):
        settings = {
            "daemon_url": "http://172.19.0.1:8555",
            "queue_token": "do-not-log-this",
            "n8n_url": "http://n8n:5678",
            "webhook_token": "do-not-log-this-either",
            "trigger_enabled": False,
        }
        notification = {
            "notification_id": "notice-001",
            "batch_id": "batch-001",
            "step": "notebooklm",
            "message": "NotebookLM authentication failed",
            "items": [
                {"title": "Tabela IBS e CBS", "drive_file_id": "drive-file-001"},
                {"title": "Nota Técnica 2026.008 v1.01"},
            ],
            "created_at": "2026-11-02T10:00:00Z",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("hermes.nfe_gatekeeper.load_settings", return_value=settings),
                patch(
                    "hermes.nfe_gatekeeper.pending_failure_notifications",
                    side_effect=[[notification], []],
                ),
                patch("hermes.nfe_gatekeeper.pending_batch", return_value=None),
                patch("hermes.nfe_gatekeeper.send_telegram") as send,
                patch(
                    "hermes.nfe_gatekeeper.request_json",
                    return_value={"ok": True, "status": "acknowledged"},
                ) as request,
            ):
                first = poll_once(state_file=Path(temporary) / "state.json")
                second = poll_once(state_file=Path(temporary) / "state.json")

        self.assertEqual(first["status"], "failure_notified")
        self.assertEqual(first["failure_notification_count"], 1)
        self.assertEqual(second["status"], "empty")
        send.assert_called_once()
        message = send.call_args.args[0]
        self.assertIn("Tabela IBS e CBS", message)
        self.assertIn("Nota Técnica 2026.008 v1.01", message)
        self.assertIn("NotebookLM authentication failed", message)
        self.assertIn("continuam lá", message)
        request.assert_called_once_with(
            "http://172.19.0.1:8555/api/queue/resolve",
            settings["queue_token"],
            {
                "batch_id": "batch-001",
                "action": "ack_notification",
                "notification_ids": ["notice-001"],
            },
        )

    def test_failure_message_does_not_offer_an_approval_action(self):
        message = format_failure_message(
            {
                "batch_id": "batch-001",
                "items": [{"title": "Failed document"}],
                "message": "Cookie expired",
            }
        )
        self.assertIn("Failed document", message)
        self.assertIn("Cookie expired", message)
        self.assertNotIn("Aprovar", message)
        self.assertNotIn("Rejeitar", message)

    def test_rejection_resolves_without_triggering_flow_b(self):
        settings = {
            "daemon_url": "http://172.19.0.1:8555",
            "queue_token": "do-not-log-this",
            "n8n_url": "http://n8n:5678",
            "webhook_token": "do-not-log-this-either",
        }
        batch = {"batch_id": "batch-001", "items": []}
        with (
            patch("hermes.nfe_gatekeeper.load_settings", return_value=settings),
            patch("hermes.nfe_gatekeeper.pending_batch", return_value=batch),
            patch(
                "hermes.nfe_gatekeeper.request_json",
                return_value={"ok": True, "status": "rejected"},
            ) as request,
        ):
            result = decide_message(
                "batch-001",
                123456789,
                "batch-001",
                "Rejeitar",
            )
        self.assertEqual(result["status"], "rejected")
        request.assert_called_once_with(
            "http://172.19.0.1:8555/api/queue/resolve",
            settings["queue_token"],
            {"batch_id": "batch-001", "action": "reject", "chat_id": 123456789},
        )

    def test_approval_triggers_only_after_daemon_confirmation(self):
        settings = {
            "daemon_url": "http://172.19.0.1:8555",
            "queue_token": "do-not-log-this",
            "n8n_url": "http://n8n:5678",
            "webhook_token": "do-not-log-this-either",
            "trigger_enabled": True,
        }
        batch = {"batch_id": "batch-001", "items": []}
        with (
            patch("hermes.nfe_gatekeeper.load_settings", return_value=settings),
            patch("hermes.nfe_gatekeeper.pending_batch", return_value=batch),
            patch(
                "hermes.nfe_gatekeeper.request_json",
                side_effect=[
                    {"ok": True, "status": "approved"},
                    {"ok": True, "batch_id": "batch-001", "status": "accepted"},
                ],
            ) as request,
        ):
            result = decide_message(
                "batch-001",
                123456789,
                "batch-001",
                "Aprovar",
            )
        self.assertEqual(result["distribution"], "accepted")
        self.assertEqual(
            request.call_args_list,
            [
                call(
                    "http://172.19.0.1:8555/api/queue/resolve",
                    settings["queue_token"],
                    {"batch_id": "batch-001", "action": "approve", "chat_id": 123456789},
                ),
                call(
                    "http://n8n:5678/webhook/nfe-aprovado-interno",
                    settings["webhook_token"],
                    {"batch_id": "batch-001"},
                ),
            ],
        )

    def test_stubbed_approval_never_calls_flux_b(self):
        settings = {
            "daemon_url": "http://172.19.0.1:8555",
            "queue_token": "do-not-log-this",
            "n8n_url": "http://n8n:5678",
            "webhook_token": "do-not-log-this-either",
            "trigger_enabled": False,
        }
        with (
            patch("hermes.nfe_gatekeeper.load_settings", return_value=settings),
            patch(
                "hermes.nfe_gatekeeper.pending_batch",
                return_value={"batch_id": "batch-001", "items": []},
            ),
            patch(
                "hermes.nfe_gatekeeper.request_json",
                return_value={"ok": True, "status": "approved"},
            ) as request,
        ):
            result = decide_message(
                "batch-001",
                123456789,
                "batch-001",
                "Aprovar",
            )
        self.assertEqual(result["distribution"], "stubbed")
        request.assert_called_once()

    def test_decision_rejects_non_oldest_batch_before_mutation(self):
        settings = {
            "daemon_url": "http://172.19.0.1:8555",
            "queue_token": "do-not-log-this",
            "n8n_url": "http://n8n:5678",
            "webhook_token": "do-not-log-this-either",
        }
        with (
            patch("hermes.nfe_gatekeeper.load_settings", return_value=settings),
            patch(
                "hermes.nfe_gatekeeper.pending_batch",
                return_value={"batch_id": "oldest", "items": []},
            ),
            patch("hermes.nfe_gatekeeper.request_json") as request,
            self.assertRaises(GatekeeperError),
        ):
            decide_message(
                "another-batch",
                123456789,
                "another-batch",
                "Aprovar",
            )
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
