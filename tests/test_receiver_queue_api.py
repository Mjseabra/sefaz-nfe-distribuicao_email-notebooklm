import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notebooklm_receiver as receiver


class QueueAPITests(unittest.TestCase):
    TOKENS = {
        "QUEUE_FLOW_A_TOKEN": "test-flow-a",
        "QUEUE_HERMES_TOKEN": "test-hermes",
        "QUEUE_FLOW_B_TOKEN": "test-flow-b",
        "QUEUE_OPERATOR_TOKEN": "test-operator",
    }

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cache_dir = self.root / "pdf-cache"
        self.cache_dir.mkdir()
        self.pdf_path = self.cache_dir / "test.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4 test fixture")
        self.catalog_path = self.root / "catalog.json"
        self.queue_path = self.root / "fila_pendencias.json"
        self.constants = [
            patch.object(receiver, "CACHE_DIR", str(self.cache_dir)),
            patch.object(receiver, "CATALOG_PATH", str(self.catalog_path)),
            patch.object(receiver, "QUEUE_PATH", str(self.queue_path)),
            patch.object(receiver, "QUEUE_LOCK", threading.RLock()),
        ]
        for constant in self.constants:
            constant.start()
            self.addCleanup(constant.stop)
        self.env_patch = patch.dict(os.environ, self.TOKENS, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.server = HTTPServer(("127.0.0.1", 0), receiver.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, *, method="GET", body=None, role=None, token=None):
        headers = {}
        if role:
            headers["Authorization"] = f"Bearer {token or self.TOKENS[role]}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            result = json.loads(raw.decode("utf-8"))
            content_type = response.headers.get("Content-Type", "")
            return response.status, content_type, result

    def add_batch(self, title="Test NFe"):
        body = {
            "items": [
                {
                    "title": title,
                    "date": date.today().isoformat(),
                    "url": "https://example.invalid/nfe/1",
                    "file_path": str(self.pdf_path),
                    "document_id": "test-nfe-1",
                }
            ]
        }
        return self.request(
            "/api/queue/add", method="POST", body=body, role="QUEUE_FLOW_A_TOKEN"
        )

    def approve(self, batch_id):
        return self.request(
            "/api/queue/resolve",
            method="POST",
            body={"batch_id": batch_id, "action": "approve", "chat_id": 123456789},
            role="QUEUE_HERMES_TOKEN",
        )

    def test_add_and_list_require_role_scoped_credentials_and_return_json(self):
        status, content_type, added = self.add_batch()
        self.assertEqual(status, 201)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(added["added"], 1)
        self.assertEqual(added["skipped"], 0)

        status, content_type, listed = self.request(
            "/api/queue/list?state=pending", role="QUEUE_HERMES_TOKEN"
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(listed["batches"][0]["batch_id"], added["batch_id"])

        status, _, error = self.request(
            "/api/queue/add",
            method="POST",
            body={"items": []},
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(status, 403)
        self.assertFalse(error["ok"])

    def test_add_rejects_missing_auth_and_invalid_input_as_json(self):
        status, content_type, error = self.request(
            "/api/queue/add", method="POST", body={"items": []}
        )
        self.assertEqual(status, 401)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertFalse(error["ok"])

        missing_field = {
            "items": [
                {
                    "title": "Missing Date",
                    "url": "https://example.invalid/nfe/3",
                    "file_path": str(self.pdf_path),
                }
            ]
        }
        status, content_type, error = self.request(
            "/api/queue/add",
            method="POST",
            body=missing_field,
            role="QUEUE_FLOW_A_TOKEN",
        )
        self.assertEqual(status, 422)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertFalse(error["ok"])

        bad_path = {
            "items": [
                {
                    "title": "Outside PDF",
                    "date": date.today().isoformat(),
                    "url": "https://example.invalid/nfe/2",
                    "file_path": str(self.root / "outside.pdf"),
                }
            ]
        }
        status, content_type, error = self.request(
            "/api/queue/add",
            method="POST",
            body=bad_path,
            role="QUEUE_FLOW_A_TOKEN",
        )
        self.assertEqual(status, 422)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertFalse(error["ok"])

    def test_add_retry_is_idempotent(self):
        first_status, _, first = self.add_batch()
        retry_status, _, retry = self.add_batch()

        self.assertEqual(first_status, 201)
        self.assertEqual(retry_status, 200)
        self.assertEqual(retry["batch_id"], first["batch_id"])
        self.assertEqual(retry["added"], 0)
        self.assertEqual(retry["skipped"], 1)

    def test_approval_moves_batch_and_preserves_pdf_path(self):
        _, _, added = self.add_batch()
        self.assertEqual(self.approve(added["batch_id"])[0], 200)

        pending_status, _, pending = self.request(
            "/api/queue/list?state=pending", role="QUEUE_HERMES_TOKEN"
        )
        approved_status, _, approved = self.request(
            f"/api/queue/list?state=approved&batch_id={added['batch_id']}",
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(pending_status, 200)
        self.assertEqual(pending["batches"], [])
        self.assertEqual(approved_status, 200)
        self.assertEqual(
            approved["batches"][0]["items"][0]["file_path"], str(self.pdf_path)
        )

    def test_wrong_chat_cannot_approve(self):
        _, _, added = self.add_batch()
        status, content_type, error = self.request(
            "/api/queue/resolve",
            method="POST",
            body={
                "batch_id": added["batch_id"],
                "action": "approve",
                "chat_id": 1,
            },
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(status, 403)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertFalse(error["ok"])

    def test_invalid_queue_state_and_wrong_method_return_structured_json(self):
        self.queue_path.write_text('{"schema_version": 1,', encoding="utf-8")
        status, content_type, error = self.request(
            "/api/queue/list?state=pending", role="QUEUE_HERMES_TOKEN"
        )
        self.assertEqual(status, 500)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(error["error"]["code"], "QUEUE_STATE_INVALID")

        status, content_type, error = self.request(
            "/api/queue/list?state=pending",
            method="POST",
            body={},
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(status, 405)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(error["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_rejection_catalog_write_and_retry_are_idempotent(self):
        _, _, added = self.add_batch()
        request = {
            "batch_id": added["batch_id"],
            "action": "reject",
            "chat_id": 123456789,
        }
        first_status, _, first = self.request(
            "/api/queue/resolve",
            method="POST",
            body=request,
            role="QUEUE_HERMES_TOKEN",
        )
        retry_status, _, retry = self.request(
            "/api/queue/resolve",
            method="POST",
            body=request,
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(first_status, 200)
        self.assertEqual(retry_status, 200)
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(retry["status"], "rejected")

        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["sources"]), 1)
        self.assertEqual(catalog["sources"][0]["key"], "testnfe")
        _, _, pending = self.request(
            "/api/queue/list?state=pending", role="QUEUE_HERMES_TOKEN"
        )
        self.assertEqual(pending["batches"], [])

    def test_rejected_document_is_filtered_from_catalog_on_resubmission(self):
        title = f"Rejected NFe {date.today().strftime('%d/%m/%Y')}"
        _, _, added = self.add_batch(title=title)
        status, content_type, rejected = self.request(
            "/api/queue/resolve",
            method="POST",
            body={
                "batch_id": added["batch_id"],
                "action": "reject",
                "chat_id": 123456789,
            },
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(rejected["status"], "rejected")

        status, content_type, result = self.request(
            "/api/catalog/filter-new",
            method="POST",
            body={
                "items": [
                    {
                        "title": title,
                        "desc": "",
                        "href": "https://example.invalid/rejected",
                    }
                ]
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["rejected"][0]["reason"], "ja_existe_no_catalogo")

    def test_delivery_steps_are_ordered_and_completion_is_idempotent(self):
        _, _, added = self.add_batch()
        self.approve(added["batch_id"])

        def record(step, outcome, role="QUEUE_FLOW_B_TOKEN"):
            return self.request(
                "/api/queue/resolve",
                method="POST",
                body={
                    "batch_id": added["batch_id"],
                    "action": "record_step",
                    "step": step,
                    "outcome": outcome,
                },
                role=role,
            )

        status, content_type, error = record("google_drive", "started")
        self.assertEqual(status, 409)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertFalse(error["ok"])

        for step in ("email", "google_drive", "notebooklm"):
            self.assertEqual(record(step, "started")[0], 200)
            self.assertEqual(record(step, "succeeded")[0], 200)

        complete_request = {
            "batch_id": added["batch_id"],
            "action": "complete",
        }
        first_status, _, first = self.request(
            "/api/queue/resolve",
            method="POST",
            body=complete_request,
            role="QUEUE_FLOW_B_TOKEN",
        )
        retry_status, _, retry = self.request(
            "/api/queue/resolve",
            method="POST",
            body=complete_request,
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(first_status, 200)
        self.assertEqual(retry_status, 200)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(retry["status"], "complete")

        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["sources"]), 1)
        approved_status, _, approved = self.request(
            f"/api/queue/list?state=approved&batch_id={added['batch_id']}",
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(approved_status, 404)
        self.assertFalse(approved["ok"])

    def test_uncertain_step_requires_operator_reconciliation(self):
        _, _, added = self.add_batch()
        self.approve(added["batch_id"])
        request = {
            "batch_id": added["batch_id"],
            "action": "record_step",
            "step": "email",
            "outcome": "started",
        }
        self.assertEqual(
            self.request(
                "/api/queue/resolve",
                method="POST",
                body=request,
                role="QUEUE_FLOW_B_TOKEN",
            )[0],
            200,
        )
        request["outcome"] = "uncertain"
        self.assertEqual(
            self.request(
                "/api/queue/resolve",
                method="POST",
                body=request,
                role="QUEUE_FLOW_B_TOKEN",
            )[0],
            200,
        )
        request["outcome"] = "not_started"
        self.assertEqual(
            self.request(
                "/api/queue/resolve",
                method="POST",
                body=request,
                role="QUEUE_FLOW_B_TOKEN",
            )[0],
            403,
        )
        request["outcome"] = "succeeded"
        status, _, _ = self.request(
            "/api/queue/resolve",
            method="POST",
            body=request,
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(status, 403)

    def test_notebooklm_failure_notifications_are_private_and_acknowledged_by_hermes(self):
        _, _, added = self.add_batch(title="Tabela IBS e CBS")
        self.approve(added["batch_id"])

        def record(step, outcome, **extra):
            return self.request(
                "/api/queue/resolve",
                method="POST",
                body={
                    "batch_id": added["batch_id"],
                    "action": "record_step",
                    "step": step,
                    "outcome": outcome,
                    **extra,
                },
                role="QUEUE_FLOW_B_TOKEN",
            )

        for step in ("email", "google_drive"):
            self.assertEqual(record(step, "started")[0], 200)
            self.assertEqual(record(step, "succeeded")[0], 200)
        self.assertEqual(record("notebooklm", "started")[0], 200)
        invalid_status, _, invalid_report = record(
            "notebooklm",
            "uncertain",
            failure={
                "items": [{"title": "Not in this batch"}],
                "message": "Synthetic failure",
            },
        )
        self.assertEqual(invalid_status, 422)
        self.assertEqual(invalid_report["error"]["code"], "INVALID_FAILURE_ITEM")
        status, _, uncertain = record(
            "notebooklm",
            "uncertain",
            failure={
                "items": [
                    {
                        "title": "Tabela IBS e CBS",
                        "drive_file_id": "drive-file-001",
                    }
                ],
                "message": "NotebookLM authentication failed",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(uncertain["status"], "needs_reconciliation")

        status, content_type, response = self.request(
            "/api/queue/list?state=notifications",
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(len(response["notifications"]), 1)
        notification = response["notifications"][0]
        self.assertEqual(notification["batch_id"], added["batch_id"])
        self.assertEqual(notification["items"][0]["title"], "Tabela IBS e CBS")
        self.assertEqual(notification["items"][0]["drive_file_id"], "drive-file-001")
        self.assertNotIn("file_path", json.dumps(notification))

        status, _, acknowledged = self.request(
            "/api/queue/resolve",
            method="POST",
            body={
                "batch_id": added["batch_id"],
                "action": "ack_notification",
                "notification_ids": [notification["notification_id"]],
            },
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(status, 200)
        self.assertEqual(acknowledged["status"], "acknowledged")
        _, _, remaining = self.request(
            "/api/queue/list?state=notifications",
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(remaining["notifications"], [])
        status, _, approved = self.request(
            f"/api/queue/list?state=approved&batch_id={added['batch_id']}",
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["batches"][0]["delivery_status"], "needs_reconciliation")
        self.assertEqual(
            approved["batches"][0]["items"][0]["delivery"]["notebooklm"],
            "uncertain",
        )
        status, _, completion_error = self.request(
            "/api/queue/resolve",
            method="POST",
            body={"batch_id": added["batch_id"], "action": "complete"},
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(status, 409)
        self.assertEqual(completion_error["error"]["code"], "DELIVERY_INCOMPLETE")

    def test_notebooklm_sync_error_includes_failed_document_identity(self):
        failed = type(
            "CompletedProcess",
            (),
            {"returncode": 1, "stderr": "NotebookLM authentication failed", "stdout": ""},
        )()
        with patch("notebooklm_receiver.subprocess.run", return_value=failed):
            status, content_type, response = self.request(
                "/api/sync-notebooklm",
                method="POST",
                body={
                    "drive_file_id": "drive-file-001",
                    "document_title": "Nota Técnica 2026.008 v1.01",
                    "document_id": "nt-2026-008",
                    "mime_type": "pdf",
                },
            )
        self.assertEqual(status, 500)
        self.assertTrue(content_type.startswith("application/json"))
        self.assertEqual(response["title"], "Nota Técnica 2026.008 v1.01")
        self.assertEqual(response["drive_file_id"], "drive-file-001")
        self.assertIn("authentication failed", response["error"])


if __name__ == "__main__":
    unittest.main()
