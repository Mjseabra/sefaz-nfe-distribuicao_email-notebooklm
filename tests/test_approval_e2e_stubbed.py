import json
import os
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notebooklm_receiver as receiver


class ApprovalE2ETests(unittest.TestCase):
    TOKENS = {
        "QUEUE_FLOW_A_TOKEN": "e2e-flow-a",
        "QUEUE_HERMES_TOKEN": "e2e-hermes",
        "QUEUE_FLOW_B_TOKEN": "e2e-flow-b",
        "QUEUE_OPERATOR_TOKEN": "e2e-operator",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "pdf-cache"
        self.cache.mkdir()
        self.pdf = self.cache / "fixture.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fixture")
        self.patches = [
            patch.object(receiver, "CACHE_DIR", str(self.cache)),
            patch.object(receiver, "CATALOG_PATH", str(self.root / "catalog.json")),
            patch.object(receiver, "QUEUE_PATH", str(self.root / "queue.json")),
            patch.object(receiver, "QUEUE_LOCK", threading.RLock()),
            patch.dict(os.environ, self.TOKENS, clear=False),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), receiver.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.temp.cleanup()

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, *, role, method="GET", body=None):
        headers = {"Authorization": f"Bearer {self.TOKENS[role]}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
            return response.status, payload

    def add_batch(self, title, document_id="e2e-document"):
        return self.request(
            "/api/queue/add",
            role="QUEUE_FLOW_A_TOKEN",
            method="POST",
            body={
                "items": [
                    {
                        "title": title,
                        "date": date.today().isoformat(),
                        "url": "https://example.invalid/e2e",
                        "file_path": str(self.pdf),
                        "document_id": document_id,
                    }
                ]
            },
        )

    def resolve(self, batch_id, action, role):
        body = {"batch_id": batch_id, "action": action}
        if action in ("approve", "reject"):
            body["chat_id"] = 123456789
        return self.request(
            "/api/queue/resolve",
            role=role,
            method="POST",
            body=body,
        )

    def test_stubbed_approval_distribution_order_and_completion(self):
        status, added = self.add_batch("E2E approval document")
        self.assertEqual(status, 201)
        batch_id = added["batch_id"]

        status, pending = self.request(
            "/api/queue/list?state=pending", role="QUEUE_HERMES_TOKEN"
        )
        self.assertEqual(status, 200)
        document = pending["batches"][0]["items"][0]
        self.assertEqual(document["title"], "E2E approval document")
        self.assertEqual(document["date"], date.today().isoformat())

        status, decision = self.resolve(batch_id, "approve", "QUEUE_HERMES_TOKEN")
        self.assertEqual(status, 200)
        self.assertEqual(decision["status"], "approved")

        status, approved = self.request(
            f"/api/queue/list?state=approved&batch_id={batch_id}",
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["batches"][0]["items"][0]["file_path"], str(self.pdf))

        effects = []
        for step in ("email", "google_drive", "notebooklm"):
            status, _ = self.request(
                "/api/queue/resolve",
                role="QUEUE_FLOW_B_TOKEN",
                method="POST",
                body={
                    "batch_id": batch_id,
                    "action": "record_step",
                    "step": step,
                    "outcome": "started",
                },
            )
            self.assertEqual(status, 200)
            effects.append(step)
            status, _ = self.request(
                "/api/queue/resolve",
                role="QUEUE_FLOW_B_TOKEN",
                method="POST",
                body={
                    "batch_id": batch_id,
                    "action": "record_step",
                    "step": step,
                    "outcome": "succeeded",
                },
            )
            self.assertEqual(status, 200)

        self.assertEqual(effects, ["email", "google_drive", "notebooklm"])
        status, completed = self.resolve(
            batch_id, "complete", "QUEUE_FLOW_B_TOKEN"
        )
        self.assertEqual(status, 200)
        self.assertEqual(completed["status"], "complete")
        status, unavailable = self.request(
            f"/api/queue/list?state=approved&batch_id={batch_id}",
            role="QUEUE_FLOW_B_TOKEN",
        )
        self.assertEqual(status, 404)
        self.assertFalse(unavailable["ok"])

    def test_rejection_prevents_resubmission_and_runs_no_distribution_stubs(self):
        title = f"E2E rejected document {date.today().isoformat()}"
        status, added = self.add_batch(title)
        self.assertEqual(status, 201)

        status, rejected = self.resolve(
            added["batch_id"], "reject", "QUEUE_HERMES_TOKEN"
        )
        self.assertEqual(status, 200)
        self.assertEqual(rejected["status"], "rejected")

        status, retry = self.add_batch(title)
        self.assertEqual(status, 200)
        self.assertEqual(retry["added"], 0)
        self.assertEqual(retry["skipped"], 1)

        filter_status, filtered = self.request(
            "/api/catalog/filter-new",
            role="QUEUE_FLOW_A_TOKEN",
            method="POST",
            body={
                "items": [
                    {
                        "title": title,
                        "desc": f"Publicacao em {date.today().strftime('%d/%m/%Y')}",
                        "href": "https://example.invalid/e2e-rejected",
                    }
                ]
            },
        )
        self.assertEqual(filter_status, 200)
        self.assertEqual(filtered["count"], 0)
        self.assertEqual(
            filtered["rejected"][0]["reason"], "ja_existe_no_catalogo"
        )

        status, pending = self.request(
            "/api/queue/list?state=pending", role="QUEUE_HERMES_TOKEN"
        )
        self.assertEqual(status, 200)
        self.assertEqual(pending["batches"], [])

        catalog = json.loads((self.root / "catalog.json").read_text(encoding="utf-8"))
        self.assertTrue(
            any(item["original_title"] == title for item in catalog["sources"])
        )

    def test_provider_failure_at_each_step_requires_reconciliation_before_retry(self):
        steps = ("email", "google_drive", "notebooklm")

        for failed_step in steps:
            with self.subTest(step=failed_step):
                title = f"E2E failure recovery {failed_step}"
                status, added = self.add_batch(
                    title, document_id=f"failure-{failed_step}"
                )
                self.assertEqual(status, 201)
                batch_id = added["batch_id"]
                status, decision = self.resolve(
                    batch_id, "approve", "QUEUE_HERMES_TOKEN"
                )
                self.assertEqual(status, 200)
                self.assertEqual(decision["status"], "approved")

                attempts = {step: 0 for step in steps}
                effects = {step: 0 for step in steps}

                def record(step, outcome, role="QUEUE_FLOW_B_TOKEN"):
                    return self.request(
                        "/api/queue/resolve",
                        role=role,
                        method="POST",
                        body={
                            "batch_id": batch_id,
                            "action": "record_step",
                            "step": step,
                            "outcome": outcome,
                        },
                    )

                for step in steps:
                    started_status, _ = record(step, "started")
                    self.assertEqual(started_status, 200)
                    attempts[step] += 1

                    if step == failed_step:
                        uncertain_status, uncertain = record(step, "uncertain")
                        self.assertEqual(uncertain_status, 200)
                        self.assertEqual(uncertain["status"], "needs_reconciliation")

                        replay_status, replay = record(step, "started")
                        self.assertEqual(replay_status, 409)
                        self.assertEqual(
                            replay["error"]["code"], "RECONCILIATION_REQUIRED"
                        )

                        complete_status, incomplete = self.resolve(
                            batch_id, "complete", "QUEUE_FLOW_B_TOKEN"
                        )
                        self.assertEqual(complete_status, 409)
                        self.assertEqual(
                            incomplete["error"]["code"], "DELIVERY_INCOMPLETE"
                        )

                        list_status, approved = self.request(
                            f"/api/queue/list?state=approved&batch_id={batch_id}",
                            role="QUEUE_FLOW_B_TOKEN",
                        )
                        self.assertEqual(list_status, 200)
                        self.assertEqual(
                            approved["batches"][0]["items"][0]["delivery"][step],
                            "uncertain",
                        )

                        reset_status, reset = record(
                            step, "not_started", role="QUEUE_OPERATOR_TOKEN"
                        )
                        self.assertEqual(reset_status, 200)
                        self.assertEqual(reset["status"], "approved")
                        started_status, _ = record(step, "started")
                        self.assertEqual(started_status, 200)
                        attempts[step] += 1

                    effects[step] += 1
                    succeeded_status, _ = record(step, "succeeded")
                    self.assertEqual(succeeded_status, 200)

                complete_status, completed = self.resolve(
                    batch_id, "complete", "QUEUE_FLOW_B_TOKEN"
                )
                self.assertEqual(complete_status, 200)
                self.assertEqual(completed["status"], "complete")
                self.assertEqual(attempts[failed_step], 2)
                self.assertEqual(effects, {step: 1 for step in steps})
