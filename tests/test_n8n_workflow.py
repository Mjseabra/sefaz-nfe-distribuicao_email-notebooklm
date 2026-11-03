import json
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = WORKSPACE / "alerta_zen_atual.json"


class N8nApprovalWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = WORKFLOW_PATH.read_bytes()
        encoding = "utf-16" if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff") else "utf-8"
        workflows = json.loads(raw.decode(encoding))
        cls.workflow = workflows[0]
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}
        cls.nodes_by_id = {node["id"]: node for node in cls.workflow["nodes"]}
        cls.connections = cls.workflow["connections"]

    def node(self, name):
        self.assertIn(name, self.nodes)
        return self.nodes[name]

    def node_by_id(self, node_id):
        self.assertIn(node_id, self.nodes_by_id)
        return self.nodes_by_id[node_id]

    def successors(self, node_name):
        outputs = self.connections.get(node_name, {}).get("main", [])
        return [edge["node"] for output in outputs for edge in output]

    def reachable(self, start, target):
        seen = set()
        pending = [start]
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(self.successors(current))
        return False

    def test_existing_workflow_is_preserved_and_queue_add_is_after_shared_file_write(self):
        self.assertIn("SEFAZ NF-e", self.workflow["name"])
        self.assertEqual(self.node_by_id("node-baixar-pdf")["name"], "Baixar Arquivo")
        self.assertTrue(
            self.reachable("Filtrar Novos pelo Cat├ílogo", "Baixar Arquivo")
        )
        self.assertEqual(
            self.successors("Baixar Arquivo"), ["Preparar Arquivo da Fila"]
        )
        self.assertEqual(
            self.successors("Salvar PDF Compartilhado"),
            ["Preparar Payload da Fila"],
        )
        save_node = self.node("Salvar PDF Compartilhado")
        self.assertEqual(save_node["parameters"]["operation"], "write")
        self.assertEqual(
            save_node["parameters"]["fileName"], "={{ $json.file_path }}"
        )
        self.assertEqual(
            save_node["parameters"]["dataPropertyName"], "anexo_arquivo"
        )

        add_node = self.node("Adicionar Documentos a Fila")
        self.assertEqual(add_node["parameters"]["method"], "POST")
        self.assertEqual(
            add_node["parameters"]["url"],
            "http://notebooklm_receiver:8555/api/queue/add",
        )
        self.assertEqual(add_node["parameters"]["options"]["timeout"], 30000)
        self.assertEqual(
            add_node["credentials"]["httpHeaderAuth"]["id"], "nfeDaemonFlowA01"
        )
        self.assertFalse(
            self.reachable("Webhook do Changedetection", "Enviar Email SMTP")
        )

    def test_rejected_catalog_items_cannot_reach_the_queue_add_node(self):
        catalog_filter = self.node("Filtrar Novos pelo Cat├ílogo")
        self.assertEqual(
            catalog_filter["parameters"]["url"],
            "http://notebooklm_receiver:8555/api/catalog/filter-new",
        )
        self.assertEqual(catalog_filter["parameters"]["method"], "POST")
        self.assertEqual(
            self.successors("Filtrar Novos pelo Cat├ílogo"),
            ["Preparar Documentos Aprovados"],
        )
        self.assertFalse(
            self.reachable("Preparar Documentos Aprovados", "Enviar Email SMTP")
        )
        self.assertTrue(
            self.reachable("Preparar Documentos Aprovados", "Adicionar Documentos a Fila")
        )

    def test_prepared_pdf_preserves_named_binary_field_for_shared_file_write(self):
        prepare_file = self.node("Preparar Arquivo da Fila")
        code = prepare_file["parameters"]["jsCode"]
        self.assertIn("const binary=$binary?.anexo_arquivo", code)
        self.assertIn("binary:{anexo_arquivo:binary}", code)
        self.assertEqual(
            self.node("Salvar PDF Compartilhado")["parameters"]["dataPropertyName"],
            "anexo_arquivo",
        )

    def test_only_approved_internal_flow_can_reach_fixed_test_email(self):
        webhook = self.node("Webhook do Fluxo B")
        self.assertEqual(webhook["parameters"]["httpMethod"], "POST")
        self.assertEqual(webhook["parameters"]["path"], "nfe-aprovado-interno")
        self.assertEqual(webhook["parameters"]["responseMode"], "responseNode")
        self.assertEqual(webhook["parameters"]["authentication"], "headerAuth")
        self.assertEqual(
            webhook["credentials"]["httpHeaderAuth"]["id"], "nfeHermesWebhk01"
        )
        self.assertEqual(
            self.successors("Webhook do Fluxo B"),
            ["Validar Payload Hermes"],
        )
        self.assertTrue(
            self.reachable("Validar Lote Aprovado", "Enviar Email SMTP")
        )
        self.assertFalse(
            self.reachable("Webhook do Changedetection", "Enviar Email SMTP")
        )

        email = self.node_by_id("email-zen-02")
        parameters = email["parameters"]
        recipients = parameters["toEmail"].split(",")
        self.assertGreaterEqual(len(recipients), 1)
        self.assertTrue(all("@" in r for r in recipients))
        self.assertEqual(parameters.get("ccEmail", ""), "")
        self.assertEqual(parameters.get("bccEmail", ""), "")
        self.assertEqual(parameters["options"]["attachments"], "={{ $json.attachmentKeys }}")
        for node in self.workflow["nodes"]:
            if node["type"] == "n8n-nodes-base.emailSend":
                self.assertEqual(node["parameters"]["toEmail"], parameters["toEmail"])

    def test_daemon_requests_use_internal_dns_json_and_explicit_timeouts(self):
        expected_routes = {
            "/api/queue/add",
            "/api/queue/list?state=approved&batch_id=",
            "/api/queue/resolve",
        }
        daemon_nodes = [
            node
            for node in self.workflow["nodes"]
            if node["type"] == "n8n-nodes-base.httpRequest"
            and "notebooklm_receiver:8555" in node.get("parameters", {}).get("url", "")
        ]
        routes = {
            node["parameters"]["url"].split("8555", 1)[1]
            for node in daemon_nodes
            if "/api/queue/" in node["parameters"]["url"]
        }
        for expected in expected_routes:
            self.assertTrue(
                any(route.startswith(expected) for route in routes),
                f"Missing daemon route {expected}: {routes}",
            )
        for node in daemon_nodes:
            url = node["parameters"].get("url", "")
            if "/api/queue/" in url:
                self.assertEqual(node["parameters"]["options"]["timeout"], 30000)
                self.assertEqual(
                    node["parameters"]["options"]["response"]["response"][
                        "responseFormat"
                    ],
                    "json",
                )
                headers = node["parameters"]["headerParameters"]["parameters"]
                self.assertEqual(
                    node["parameters"]["genericAuthType"], "httpHeaderAuth"
                )
                self.assertIn("httpHeaderAuth", node["credentials"])
                self.assertFalse(
                    any(header["name"].lower() == "authorization" for header in headers)
                )
                self.assertTrue(
                    any(header["name"].lower() == "content-type" for header in headers)
                )
        all_urls = [
            node.get("parameters", {}).get("url", "")
            for node in self.workflow["nodes"]
        ]
        self.assertFalse(any("172.19.0.1:8555" in url for url in all_urls))

    def test_queue_and_business_state_are_owned_by_daemon_not_n8n_sqlite(self):
        for node in self.workflow["nodes"]:
            node_type = node.get("type", "").lower()
            serialized = json.dumps(node, ensure_ascii=True).lower()
            self.assertNotIn("sqlite", node_type, node["name"])
            self.assertNotIn("data table", node_type, node["name"])
            self.assertNotIn("n8n-nodes-base.datatable", node_type, node["name"])
            self.assertNotIn("sqlite", serialized, node["name"])

        daemon_routes = {
            node.get("parameters", {}).get("url", "").split("8555", 1)[-1]
            for node in self.workflow["nodes"]
            if node.get("type") == "n8n-nodes-base.httpRequest"
            and "notebooklm_receiver:8555/api/queue/" in node.get("parameters", {}).get("url", "")
        }
        self.assertTrue(any(route.startswith("/api/queue/add") for route in daemon_routes))
        self.assertTrue(any(route.startswith("/api/queue/list") for route in daemon_routes))
        self.assertIn("/api/queue/resolve", daemon_routes)

    def test_distribution_records_order_and_completes_only_after_all_integrations(self):
        order = [
            "Registrar Inicio do Email",
            "Enviar Email SMTP",
            "Registrar Sucesso do E-mail",
            "Registrar Inicio do Google Drive",
            "Upload Google Drive",
            "Registrar Sucesso do Google Drive",
            "Registrar Inicio do NotebookLM",
            "Sync NotebookLM",
            "Registrar Sucesso do NotebookLM",
            "Concluir Lote Aprovado",
        ]
        for first, second in zip(order, order[1:]):
            self.assertTrue(
                self.reachable(first, second),
                f"Expected ordered delivery path {first} -> {second}",
            )
        complete = self.node("Concluir Lote Aprovado")
        body = complete["parameters"]["jsonBody"]
        self.assertIn("action:'complete'", body)
        self.assertEqual(
            complete["credentials"]["httpHeaderAuth"]["id"], "nfeDaemonFlowB01"
        )

    def test_provider_errors_record_uncertain_and_stop_before_later_effects(self):
        steps = (
            ("Enviar Email SMTP", "Registrar Falha Ambigua do E-mail", "email"),
            (
                "Upload Google Drive",
                "Registrar Falha Ambigua do Google Drive",
                "google_drive",
            ),
            (
                "Sync NotebookLM",
                "Registrar Falha Ambigua do NotebookLM",
                "notebooklm",
            ),
        )
        for provider, uncertain_node, step in steps:
            with self.subTest(provider=provider):
                node = self.node(provider)
                outputs = self.connections[provider]["main"]
                if step == "notebooklm":
                    self.assertEqual(node.get("onError"), "continueRegularOutput")
                    self.assertEqual(
                        [edge["node"] for output in outputs for edge in output],
                        ["Consolidar Resultado NotebookLM"],
                    )
                else:
                    self.assertEqual(node.get("onError"), "continueErrorOutput")
                    self.assertEqual(
                        [edge["node"] for edge in outputs[1]], [uncertain_node]
                    )

                recorder = self.node(uncertain_node)
                if step == "notebooklm":
                    report = self.node("Consolidar Resultado NotebookLM")
                    self.assertIn("$input.all()", report["parameters"]["jsCode"])
                    self.assertIn("if(!failed.length)", report["parameters"]["jsCode"])
                    self.assertTrue(self.reachable(report["name"], uncertain_node))
                    result_branch = self.connections["NotebookLM sincronizado?"]["main"]
                    self.assertEqual(
                        [edge["node"] for edge in result_branch[0]],
                        ["Preparar Sucesso do NotebookLM"],
                    )
                    self.assertEqual(
                        [edge["node"] for edge in result_branch[1]],
                        [uncertain_node],
                    )
                self.assertEqual(recorder["parameters"]["method"], "POST")
                self.assertEqual(
                    recorder["parameters"]["url"],
                    "http://notebooklm_receiver:8555/api/queue/resolve",
                )
                body = recorder["parameters"]["jsonBody"]
                if step == "notebooklm":
                    self.assertIn("JSON.stringify($json)", body)
                else:
                    self.assertIn(f"step:'{step}'", body)
                    self.assertIn("outcome:'uncertain'", body)
                self.assertEqual(recorder["parameters"]["options"]["timeout"], 30000)
                self.assertFalse(
                    any(
                        self.reachable(uncertain_node, later)
                        for later, _, _ in steps
                        if later != provider
                    )
                )

    def test_no_direct_telegram_sender_or_public_telegram_webhook_is_active(self):
        telegram = self.node_by_id("node-notificar-telegram")
        self.assertTrue(telegram.get("disabled"))
        self.assertFalse(
            self.reachable("Webhook do Changedetection", telegram["name"])
        )
        self.assertFalse(
            self.reachable("Webhook do Fluxo B", telegram["name"])
        )
        for node in self.workflow["nodes"]:
            if node["type"] == "n8n-nodes-base.webhook":
                path = node.get("parameters", {}).get("path", "").lower()
                self.assertNotIn("telegram", path)
                self.assertNotIn("getupdates", path)

    def test_workflow_connections_are_valid_and_hermes_contract_requires_long_polling(self):
        names = {node["name"] for node in self.workflow["nodes"]}
        for source, connection in self.connections.items():
            self.assertIn(source, names)
            for output in connection.get("main", []):
                for edge in output:
                    self.assertIn(edge["node"], names)

        self.assertNotIn("Validar Token Hermes", names)
        self.assertNotIn("$env", json.dumps(self.workflow))
        request_validation = self.node("Validar Payload Hermes")["parameters"]["jsCode"]
        self.assertIn("Object.keys(p).length!==1", request_validation)
        self.assertIn("p.batch_id", request_validation)

        contract = (
            WORKSPACE
            / "specs"
            / "001-aprovacao-manual-nfe"
            / "hermes-gatekeeper.md"
        ).read_text(encoding="utf-8")
        self.assertIn("outbound Long Polling mode only", contract)
        self.assertIn("Do not register, expose, or use a Telegram webhook", contract)
        skill = (WORKSPACE / "hermes" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("sender chat ID", skill)
        self.assertIn("decide-message", skill)
        self.assertIn("unauthorized senders", skill)

    def test_webhook_responses_are_structured_json_objects(self):
        response_nodes = [
            node
            for node in self.workflow["nodes"]
            if node["type"] == "n8n-nodes-base.respondToWebhook"
        ]
        self.assertTrue(response_nodes)
        for node in response_nodes:
            parameters = node["parameters"]
            self.assertEqual(parameters["respondWith"], "json")
            self.assertTrue(parameters["responseBody"].startswith("={{ {"))
            self.assertNotIn("JSON.stringify", parameters["responseBody"])


if __name__ == "__main__":
    unittest.main()
