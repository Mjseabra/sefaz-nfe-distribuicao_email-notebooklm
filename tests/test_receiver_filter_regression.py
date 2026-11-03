import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notebooklm_receiver as receiver


class CatalogFilterRegressionTests(unittest.TestCase):
    TOKENS = {
        "QUEUE_FLOW_A_TOKEN": "test-flow-a",
        "QUEUE_HERMES_TOKEN": "test-hermes",
    }

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.catalog_path = self.root / "catalog.json"
        self.queue_path = self.root / "queue.json"
        self.cache_dir = self.root / "pdf-cache"
        self.cache_dir.mkdir()
        self.pdf_path = self.cache_dir / "flow-a-fixture.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4 isolated fixture")
        self.catalog_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "total_sources": 1,
                    "sources": [{"key": "catalogueddocument"}],
                }
            ),
            encoding="utf-8",
        )
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

    def request(self, path, *, method="POST", body=None, role=None):
        headers = {}
        if role:
            headers["Authorization"] = f"Bearer {self.TOKENS[role]}"
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
            result = json.loads(response.read().decode("utf-8"))
            return response.status, result

    def test_existing_filter_behavior_for_recent_old_duplicate_and_missing_dates(self):
        today = date.today()
        payload = {
            "items": [
                {
                    "title": f"Recent Notice {today.strftime('%d/%m/%Y')}",
                    "desc": "",
                    "href": "https://example.invalid/recent",
                },
                {
                    "title": f"Old Notice {(today - timedelta(days=6)).strftime('%d/%m/%Y')}",
                    "desc": "",
                    "href": "https://example.invalid/old",
                },
                {
                    "title": "Catalogued Document",
                    "desc": today.strftime("%d/%m/%Y"),
                    "href": "https://example.invalid/catalogued",
                },
                {
                    "title": "Notice Without Publication Date",
                    "desc": "",
                    "href": "https://example.invalid/no-date",
                },
            ]
        }
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/catalog/filter-new",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"], [payload["items"][0]])
        self.assertEqual(result["rejected_count"], 3)
        self.assertEqual(
            {item["reason"] for item in result["rejected"]},
            {
                "data_antiga ("
                + (today - timedelta(days=6)).strftime("%d/%m/%Y")
                + ", 6 dias atras)",
                "ja_existe_no_catalogo",
                "sem_data_de_publicacao",
            },
        )

    def test_new_version_of_catalogued_norm_passes_but_same_version_is_rejected(self):
        catalog = {
            "version": "1.0",
            "total_sources": 4,
            "sources": [
                {"key": "nt2026008v100rtcvalorliquidoproduto"},
                {"key": "nfent2026006v101rtcvinculacaopagamento"},
                {"key": "nt2026003v100danfesimplificadotipo2"},
                {"key": "it2025002v170cclasstrib"},
            ],
        }
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        today = date.today().strftime("%d/%m/%Y")
        titles = {
            "new_version": f"Nota Técnica 2026.008 v1.01 - Publicada em {today}",
            "same_version": f"Nota Técnica 2026.006 v1.01 - Publicada em {today}",
            "other_kind": f"Informe Técnico 2026.003 v1.00 - Publicado em {today}",
            "new_it_version": f"Informe Técnico 2025.002 v1.71 - Publicado em {today}",
        }
        payload = {
            "items": [
                {"title": title, "desc": "", "href": f"https://example.invalid/{name}"}
                for name, title in titles.items()
            ]
        }

        status, result = self.request("/api/catalog/filter-new", body=payload)

        self.assertEqual(status, 200)
        self.assertEqual(
            {item["title"] for item in result["items"]},
            {titles["new_version"], titles["other_kind"], titles["new_it_version"]},
        )
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["rejected"][0]["title"], titles["same_version"])

    def test_isolated_flow_a_matrix_filters_then_adds_only_one_eligible_document(self):
        today = date.today()
        catalogued_title = "Catalogued Document"
        previously_rejected_title = "Previously Rejected Document"
        catalog = {
            "version": "1.0",
            "total_sources": 2,
            "sources": [
                {"key": receiver.make_key(catalogued_title)},
                {"key": receiver.make_key(previously_rejected_title)},
            ],
        }
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        eligible = {
            "title": f"Recent Notice {today.strftime('%d/%m/%Y')}",
            "desc": "",
            "href": "https://example.invalid/recent",
        }
        payload = {
            "items": [
                eligible,
                dict(eligible),
                {
                    "title": f"Old Notice {(today - timedelta(days=6)).strftime('%d/%m/%Y')}",
                    "desc": "",
                    "href": "https://example.invalid/old",
                },
                {
                    "title": catalogued_title,
                    "desc": today.strftime("%d/%m/%Y"),
                    "href": "https://example.invalid/catalogued",
                },
                {
                    "title": previously_rejected_title,
                    "desc": today.strftime("%d/%m/%Y"),
                    "href": "https://example.invalid/rejected",
                },
            ]
        }

        filter_status, filtered = self.request(
            "/api/catalog/filter-new", body=payload
        )
        self.assertEqual(filter_status, 200)
        self.assertEqual(filtered["count"], 2)
        self.assertEqual(filtered["rejected_count"], 3)
        self.assertEqual(
            {item["title"] for item in filtered["rejected"]},
            {
                payload["items"][2]["title"],
                catalogued_title,
                previously_rejected_title,
            },
        )

        date_iso = today.isoformat()
        queue_items = [
            {
                "title": item["title"],
                "date": date_iso,
                "url": item["href"],
                "file_path": str(self.pdf_path),
                "document_id": f"recent-notice-{date_iso}",
            }
            for item in filtered["items"]
        ]
        add_status, added = self.request(
            "/api/queue/add",
            body={"items": queue_items},
            role="QUEUE_FLOW_A_TOKEN",
        )
        self.assertEqual(add_status, 201)
        self.assertEqual(added["added"], 1)
        self.assertEqual(added["skipped"], 1)

        list_status, pending = self.request(
            "/api/queue/list?state=pending",
            method="GET",
            role="QUEUE_HERMES_TOKEN",
        )
        self.assertEqual(list_status, 200)
        self.assertEqual(len(pending["batches"]), 1)
        self.assertEqual(pending["batches"][0]["status"], "pending")
        self.assertEqual(len(pending["batches"][0]["items"]), 1)
        self.assertTrue(self.queue_path.exists())


if __name__ == "__main__":
    unittest.main()
