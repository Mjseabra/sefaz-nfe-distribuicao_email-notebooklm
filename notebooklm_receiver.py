from http.server import HTTPServer, BaseHTTPRequestHandler
import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, date, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("PORT", "8555"))
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
SCRIPT_DRIVE = "/home/ubuntu/scripts/add_drive_to_notebooklm.py"
NOTEBOOK_ID = os.environ.get("NOTEBOOKLM_ID", "00934ae7-9818-4fa4-a698-2249cd5270f9")
CLI_PATH = os.environ.get("NOTEBOOKLM_CLI", "/home/ubuntu/.venvs/notebooklm/bin/notebooklm")
TELEGRAM_DECISION_CHAT_ID = int(os.environ.get("TELEGRAM_DECISION_CHAT_ID", "123456789"))
CACHE_DIR = os.environ.get("PDF_CACHE_DIR", "/home/ubuntu/nfe-pdf-cache")
CATALOG_PATH = os.environ.get(
    "CATALOG_PATH", "/home/ubuntu/data/notebooklm_catalog.json"
)
QUEUE_PATH = os.environ.get(
    "QUEUE_PATH", os.path.join(os.path.dirname(CATALOG_PATH), "fila_pendencias.json")
)
QUEUE_LOCK = threading.RLock()
MAX_QUEUE_REQUEST_BYTES = 1024 * 1024
QUEUE_TOKEN_ENV = {
    "flux_a": "QUEUE_FLOW_A_TOKEN",
    "hermes": "QUEUE_HERMES_TOKEN",
    "flux_b": "QUEUE_FLOW_B_TOKEN",
    "operator": "QUEUE_OPERATOR_TOKEN",
}


class QueueAPIError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class QueueStateError(Exception):
    pass


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _empty_queue_state():
    return {"schema_version": 1, "pending": [], "approved": [], "resolved": {}}


def _read_queue_state():
    if not os.path.exists(QUEUE_PATH):
        return _empty_queue_state()
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as queue_file:
            state = json.load(queue_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QueueStateError("Queue state is unreadable") from error

    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or not isinstance(state.get("pending"), list)
        or not isinstance(state.get("approved"), list)
        or not isinstance(state.get("resolved"), dict)
    ):
        raise QueueStateError("Queue state schema is invalid")
    return state


def _write_json_atomic(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".queue-", suffix=".tmp", dir=directory
    )
    try:
        if os.name != "nt":
            os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(data, output_file, indent=2, ensure_ascii=False)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _write_queue_state(state):
    _write_json_atomic(QUEUE_PATH, state)


def _read_catalog_for_queue():
    if not os.path.exists(CATALOG_PATH):
        return {"version": "1.0", "total_sources": 0, "sources": []}
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as catalog_file:
            catalog = json.load(catalog_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QueueAPIError(500, "CATALOG_UNREADABLE", "The catalog cannot be read") from error
    if not isinstance(catalog, dict) or not isinstance(catalog.get("sources"), list):
        raise QueueAPIError(500, "CATALOG_INVALID", "The catalog schema is invalid")
    if any(not isinstance(source, dict) for source in catalog["sources"]):
        raise QueueAPIError(500, "CATALOG_INVALID", "The catalog schema is invalid")
    return catalog


def _catalog_keys(catalog):
    return {
        source.get("key")
        for source in catalog["sources"]
        if isinstance(source.get("key"), str) and source.get("key")
    }


def _record_titles_in_catalog(items):
    catalog = _read_catalog_for_queue()
    keys = _catalog_keys(catalog)
    changed = False
    for item in items:
        title = item["title"]
        clean_title = normalize_title(title)
        key = make_key(clean_title)
        if not key or key in keys:
            continue
        catalog.setdefault("sources", []).append(
            {
                "clean_title": clean_title,
                "original_title": title,
                "key": key,
                "source_id": None,
                "registered_at": _utc_now(),
            }
        )
        keys.add(key)
        changed = True
    if changed:
        catalog["total_sources"] = len(catalog["sources"])
        catalog["last_updated"] = _utc_now()
        _write_json_atomic(CATALOG_PATH, catalog)


def _recover_staged_batches(state):
    changed = False
    for queue_name in ("pending", "approved"):
        remaining = []
        for batch in state[queue_name]:
            status = batch.get("status")
            if status not in ("rejecting", "completing"):
                remaining.append(batch)
                continue
            action = "reject" if status == "rejecting" else "complete"
            _record_titles_in_catalog(batch.get("items", []))
            result_status = "rejected" if action == "reject" else "complete"
            state["resolved"][batch["batch_id"]] = {
                "action": action,
                "status": result_status,
            }
            changed = True
        if len(remaining) != len(state[queue_name]):
            state[queue_name] = remaining
    return changed


def recover_queue_state():
    with QUEUE_LOCK:
        state = _read_queue_state()
        if _recover_staged_batches(state):
            _write_queue_state(state)

def normalize_title(title):
    if not title:
        return ""
    t = re.sub(r'^\[Z\s*-Duplicadas\s*Red\]\s*', '', title, flags=re.I).strip()
    return t

def make_key(title):
    t = normalize_title(title).lower()
    t = re.sub(r'\.(pdf|xlsx?|ods|zip|md)$', '', t)
    t = re.sub(r'[^a-z0-9]', '', t)
    return t

MONTHS_MAP = {
    'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
    'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12
}

def extract_dates(text):
    if not text:
        return [], []
    dates = []
    # 1. Padrão numérico DD/MM/AAAA ou DD.MM.AAAA
    for d, m, y in re.findall(r'(\d{1,2})[\/.](\d{1,2})[\/.](\d{2,4})', text):
        if len(y) == 2:
            y = '20' + y
        try:
            dates.append(date(int(y), int(m), int(d)))
        except:
            pass

    # 2. Padrão com mês abreviado DD-mes-AAAA ou DD/mes/AAAA
    for d, m_str, y in re.findall(r'(\d{1,2})[\/\-_ ](jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*[\/\-_ ](\d{2,4})', text, re.I):
        if len(y) == 2:
            y = '20' + y
        m = MONTHS_MAP.get(m_str.lower()[:3])
        if m:
            try:
                dates.append(date(int(y), m, int(d)))
            except:
                pass

    # 3. Anos isolados de 4 dígitos (ex: 2014, 2018)
    years = [int(y) for y in re.findall(r'\b(20[0-2][0-9])\b', text)]
    return dates, years

def load_catalog():
    if not os.path.exists(CATALOG_PATH):
        return {"version": "1.0", "total_sources": 0, "sources": []}
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Error loading catalog:", e)
        return {"version": "1.0", "total_sources": 0, "sources": []}

def save_catalog(data):
    try:
        os.makedirs(os.path.dirname(CATALOG_PATH), exist_ok=True)
        data["total_sources"] = len(data.get("sources", []))
        data["last_updated"] = datetime.utcnow().isoformat() + "Z"
        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print("Error saving catalog:", e)
        return False

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        messages = {
            400: ("BAD_REQUEST", "The request is invalid"),
            404: ("NOT_FOUND", "The requested resource was not found"),
            405: ("METHOD_NOT_ALLOWED", "The HTTP method is not allowed"),
            411: ("LENGTH_REQUIRED", "Content-Length is required"),
            413: ("REQUEST_TOO_LARGE", "The request body is too large"),
            415: ("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json"),
            501: ("NOT_IMPLEMENTED", "The HTTP method is not supported"),
        }
        error_code, default_message = messages.get(
            code, ("HTTP_ERROR", "The request could not be processed")
        )
        self.send_json(
            code,
            {
                "ok": False,
                "error": {
                    "code": error_code,
                    "message": default_message if message is None else message,
                },
            },
        )

    def _send_queue_error(self, error):
        self.send_json(
            error.status,
            {
                "ok": False,
                "error": {"code": error.code, "message": error.message},
            },
        )

    def _require_role(self, required_roles):
        if isinstance(required_roles, str):
            required_roles = {required_roles}
        else:
            required_roles = set(required_roles)

        configured_tokens = {
            role: os.environ.get(QUEUE_TOKEN_ENV[role], "")
            for role in QUEUE_TOKEN_ENV
        }
        if not any(configured_tokens[role] for role in required_roles):
            raise QueueAPIError(
                503, "AUTH_NOT_CONFIGURED", "A required service credential is not configured"
            )

        authorization = self.headers.get("Authorization", "")
        scheme, separator, supplied_token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not supplied_token:
            raise QueueAPIError(401, "UNAUTHORIZED", "A valid bearer credential is required")

        matched_roles = [
            role
            for role, configured_token in configured_tokens.items()
            if configured_token and hmac.compare_digest(supplied_token, configured_token)
        ]
        if len(matched_roles) > 1:
            raise QueueAPIError(
                503, "AUTH_CONFIGURATION_INVALID", "Service credentials must be unique"
            )
        if not matched_roles:
            raise QueueAPIError(401, "UNAUTHORIZED", "The bearer credential is invalid")
        if matched_roles[0] not in required_roles:
            raise QueueAPIError(403, "FORBIDDEN", "The service role is not permitted")
        return matched_roles[0]

    def _read_json_body(self):
        if self.headers.get_content_type() != "application/json":
            raise QueueAPIError(
                415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json"
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise QueueAPIError(411, "LENGTH_REQUIRED", "Content-Length is required")
        try:
            content_length = int(raw_length)
        except ValueError as error:
            raise QueueAPIError(400, "INVALID_CONTENT_LENGTH", "Content-Length is invalid") from error
        if content_length <= 0:
            raise QueueAPIError(400, "EMPTY_BODY", "A JSON request body is required")
        if content_length > MAX_QUEUE_REQUEST_BYTES:
            raise QueueAPIError(413, "REQUEST_TOO_LARGE", "The request body is too large")
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise QueueAPIError(400, "INVALID_JSON", "The request body is not valid JSON") from error
        if not isinstance(payload, dict):
            raise QueueAPIError(400, "INVALID_JSON_SHAPE", "The JSON body must be an object")
        return payload

    def do_GET(self):
        parsed_path = urlsplit(self.path)
        if parsed_path.path == "/api/queue/add" or parsed_path.path == "/api/queue/resolve":
            self.send_error(405)
            return
        if parsed_path.path != "/api/queue/list":
            super().do_GET()
            return

        try:
            try:
                parameters = parse_qs(
                    parsed_path.query, keep_blank_values=True, strict_parsing=True
                )
            except ValueError as error:
                raise QueueAPIError(400, "INVALID_QUERY", "The query string is invalid") from error
            if set(parameters) - {"state", "batch_id"}:
                raise QueueAPIError(400, "INVALID_QUERY", "Only state and batch_id are supported")
            if len(parameters.get("state", [])) != 1:
                raise QueueAPIError(
                    400, "INVALID_STATE", "state must be pending, approved, or notifications"
                )
            state_name = parameters["state"][0]
            if state_name not in ("pending", "approved", "notifications"):
                raise QueueAPIError(
                    400, "INVALID_STATE", "state must be pending, approved, or notifications"
                )
            if len(parameters.get("batch_id", [])) > 1:
                raise QueueAPIError(400, "INVALID_BATCH_ID", "batch_id must be provided once")
            batch_id = parameters.get("batch_id", [None])[0]
            if batch_id == "":
                raise QueueAPIError(400, "INVALID_BATCH_ID", "batch_id must not be empty")
            role = "flux_b" if state_name == "approved" else "hermes"
            self._require_role(role)

            with QUEUE_LOCK:
                state = _read_queue_state()
                if _recover_staged_batches(state):
                    _write_queue_state(state)
                if state_name == "notifications":
                    notifications = []
                    for batch in state["approved"]:
                        if batch_id and batch.get("batch_id") != batch_id:
                            continue
                        for failure in batch.get("delivery_failures", []):
                            if failure.get("notified_at"):
                                continue
                            notifications.append(
                                {
                                    "notification_id": failure["notification_id"],
                                    "batch_id": batch["batch_id"],
                                    "step": failure["step"],
                                    "message": failure["message"],
                                    "items": failure["items"],
                                    "created_at": failure["created_at"],
                                }
                            )
                    self.send_json(
                        200,
                        {"ok": True, "state": state_name, "notifications": notifications},
                    )
                    return
                batches = sorted(
                    (
                        batch
                        for batch in state[state_name]
                        if batch.get("status") == state_name
                    ),
                    key=lambda batch: batch.get("created_at", ""),
                )
                if state_name == "pending":
                    oldest = batches[:1]
                    if batch_id and (not oldest or oldest[0]["batch_id"] != batch_id):
                        raise QueueAPIError(
                            409, "NOT_OLDEST_PENDING", "Only the oldest pending batch may be read"
                        )
                    batches = oldest
                elif batch_id:
                    batches = [batch for batch in batches if batch.get("batch_id") == batch_id]
                    if not batches:
                        raise QueueAPIError(404, "BATCH_NOT_FOUND", "The approved batch was not found")
            self.send_json(200, {"ok": True, "state": state_name, "batches": batches})
        except QueueAPIError as error:
            self._send_queue_error(error)
        except QueueStateError:
            self.send_json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "QUEUE_STATE_INVALID",
                        "message": "Queue state is unreadable or has an invalid schema",
                    },
                },
            )
        except Exception as error:
            print("Queue list failed:", repr(error))
            self.send_json(
                500,
                {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Queue read failed"}},
            )

    def _validate_queue_document(self, item):
        allowed_fields = {"title", "date", "url", "file_path", "document_id"}
        if not isinstance(item, dict) or set(item) - allowed_fields:
            raise QueueAPIError(422, "INVALID_DOCUMENT", "A document has an invalid shape")
        for field in ("title", "date", "url", "file_path"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise QueueAPIError(
                    422, "INVALID_DOCUMENT", f"Document field {field} is required"
                )
        title = item["title"].strip()
        document_key = make_key(title)
        document_id = item.get("document_id", document_key)
        if not isinstance(document_id, str) or not document_id.strip():
            raise QueueAPIError(422, "INVALID_DOCUMENT_ID", "document_id must be a non-empty string")
        try:
            date.fromisoformat(item["date"])
        except ValueError as error:
            raise QueueAPIError(422, "INVALID_DATE", "date must use ISO-8601 date format") from error
        url_parts = urlsplit(item["url"])
        if url_parts.scheme not in ("http", "https") or not url_parts.netloc:
            raise QueueAPIError(422, "INVALID_URL", "url must be an absolute HTTP or HTTPS URL")
        if not os.path.isabs(item["file_path"]):
            raise QueueAPIError(422, "INVALID_FILE_PATH", "file_path must be an absolute path")
        root_path = os.path.realpath(CACHE_DIR)
        file_path = os.path.realpath(item["file_path"])
        try:
            inside_cache = os.path.commonpath((root_path, file_path)) == root_path
        except ValueError:
            inside_cache = False
        if not inside_cache or not os.path.isfile(file_path):
            raise QueueAPIError(
                422, "INVALID_FILE_PATH", "file_path must identify an existing PDF under the staging directory"
            )
        if not file_path.lower().endswith(".pdf"):
            raise QueueAPIError(422, "INVALID_FILE_PATH", "file_path must identify a PDF")
        return {
            "title": title,
            "date": item["date"],
            "url": item["url"],
            "file_path": file_path,
            "document_id": document_id.strip(),
        }

    def _queue_add(self):
        self._require_role("flux_a")
        payload = self._read_json_body()
        if set(payload) != {"items"} or not isinstance(payload.get("items"), list):
            raise QueueAPIError(422, "INVALID_ITEMS", "The request must contain only an items array")
        if not payload["items"]:
            raise QueueAPIError(422, "EMPTY_ITEMS", "At least one document is required")
        items = [self._validate_queue_document(item) for item in payload["items"]]

        with QUEUE_LOCK:
            state = _read_queue_state()
            if _recover_staged_batches(state):
                _write_queue_state(state)
            catalog = _read_catalog_for_queue()
            catalog_keys = _catalog_keys(catalog)
            catalog_ids = {
                source.get("document_id")
                for source in catalog["sources"]
                if isinstance(source.get("document_id"), str)
            }
            queued_items = [
                (batch["batch_id"], item)
                for name in ("pending", "approved")
                for batch in state[name]
                for item in batch.get("items", [])
            ]
            queued_ids = {item.get("document_id") for _, item in queued_items}
            queued_keys = {make_key(item.get("title", "")) for _, item in queued_items}
            accepted = []
            skipped = []
            existing_batch_ids = set()
            request_ids = set()
            request_keys = set()

            for item in items:
                key = make_key(item["title"])
                if (
                    item["document_id"] in request_ids
                    or key in request_keys
                ):
                    skipped.append(
                        {"document_id": item["document_id"], "reason": "duplicate_in_request"}
                    )
                elif item["document_id"] in catalog_ids or key in catalog_keys:
                    skipped.append(
                        {"document_id": item["document_id"], "reason": "already_catalogued"}
                    )
                elif item["document_id"] in queued_ids or key in queued_keys:
                    skipped.append(
                        {"document_id": item["document_id"], "reason": "already_queued"}
                    )
                    existing_batch_ids.update(
                        batch_id
                        for batch_id, existing in queued_items
                        if existing.get("document_id") == item["document_id"]
                        or make_key(existing.get("title", "")) == key
                    )
                else:
                    accepted.append(item)
                    request_ids.add(item["document_id"])
                    request_keys.add(key)

            if not accepted:
                batch_id = (
                    next(iter(existing_batch_ids))
                    if len(existing_batch_ids) == 1
                    else None
                )
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "batch_id": batch_id,
                        "added": 0,
                        "skipped": len(skipped),
                        "items": skipped,
                    },
                )
                return

            batch_id = uuid.uuid4().hex
            state["pending"].append(
                {
                    "batch_id": batch_id,
                    "created_at": _utc_now(),
                    "status": "pending",
                    "items": accepted,
                }
            )
            _write_queue_state(state)
        self.send_json(
            201,
            {
                "ok": True,
                "batch_id": batch_id,
                "added": len(accepted),
                "skipped": len(skipped),
                "items": skipped,
            },
        )

    def _queue_resolve(self):
        payload = self._read_json_body()
        action = payload.get("action")
        if action in ("approve", "reject"):
            expected_fields = {"batch_id", "action", "chat_id"}
            role = "hermes"
        elif action == "record_step":
            expected_fields = {"batch_id", "action", "step", "outcome"}
            role = {"flux_b", "operator"}
            if "failure" in payload:
                expected_fields.add("failure")
        elif action == "ack_notification":
            expected_fields = {"batch_id", "action", "notification_ids"}
            role = "hermes"
        elif action == "complete":
            expected_fields = {"batch_id", "action"}
            role = "flux_b"
        else:
            raise QueueAPIError(422, "INVALID_ACTION", "action is not supported")
        if set(payload) != expected_fields:
            raise QueueAPIError(422, "INVALID_REQUEST", "The request fields do not match the action")
        batch_id = payload.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise QueueAPIError(422, "INVALID_BATCH_ID", "batch_id must be a non-empty string")
        caller_role = self._require_role(role)

        if action in ("approve", "reject"):
            chat_id = payload.get("chat_id")
            if (
                isinstance(chat_id, bool)
                or not isinstance(chat_id, int)
                or chat_id != TELEGRAM_DECISION_CHAT_ID
            ):
                raise QueueAPIError(403, "INVALID_CHAT", "Only the authorized Telegram chat may decide")
        elif action == "record_step":
            step = payload.get("step")
            outcome = payload.get("outcome")
            if step not in ("email", "google_drive", "notebooklm"):
                raise QueueAPIError(422, "INVALID_STEP", "step is not supported")
            if outcome not in ("started", "succeeded", "uncertain", "not_started"):
                raise QueueAPIError(422, "INVALID_OUTCOME", "outcome is not supported")
            if outcome == "not_started" and caller_role != "operator":
                raise QueueAPIError(
                    403, "OPERATOR_REQUIRED", "Only an operator may reset an uncertain step"
                )
            if outcome in ("started", "uncertain") and caller_role != "flux_b":
                raise QueueAPIError(403, "FORBIDDEN", "The service role is not permitted")
            if "failure" in payload and (
                step != "notebooklm" or outcome != "uncertain"
            ):
                raise QueueAPIError(
                    422,
                    "INVALID_FAILURE_REPORT",
                    "Failure details are supported only for uncertain NotebookLM steps",
                )
            failure = payload.get("failure")
            if "failure" in payload:
                if (
                    not isinstance(failure, dict)
                    or set(failure) != {"items", "message"}
                    or not isinstance(failure.get("items"), list)
                    or not failure["items"]
                    or len(failure["items"]) > 100
                    or not isinstance(failure.get("message"), str)
                    or not failure["message"].strip()
                ):
                    raise QueueAPIError(
                        422, "INVALID_FAILURE_REPORT", "NotebookLM failure details are invalid"
                    )
                for item in failure["items"]:
                    if (
                        not isinstance(item, dict)
                        or set(item) - {"title", "drive_file_id"}
                        or not isinstance(item.get("title"), str)
                        or not item["title"].strip()
                        or (
                            "drive_file_id" in item
                            and (
                                not isinstance(item["drive_file_id"], str)
                                or not item["drive_file_id"].strip()
                            )
                        )
                    ):
                        raise QueueAPIError(
                            422, "INVALID_FAILURE_REPORT", "NotebookLM failure items are invalid"
                        )
                if len(failure["message"]) > 2000:
                    raise QueueAPIError(
                        413, "FAILURE_REPORT_TOO_LARGE", "The failure message is too long"
                    )
        elif action == "ack_notification":
            notification_ids = payload.get("notification_ids")
            if (
                not isinstance(notification_ids, list)
                or not notification_ids
                or len(notification_ids) > 100
                or any(
                    not isinstance(notification_id, str)
                    or not notification_id.strip()
                    for notification_id in notification_ids
                )
            ):
                raise QueueAPIError(
                    422, "INVALID_NOTIFICATION_IDS", "notification_ids must be a non-empty list"
                )
            if len(set(notification_ids)) != len(notification_ids):
                raise QueueAPIError(
                    422, "INVALID_NOTIFICATION_IDS", "notification_ids must be unique"
                )

        with QUEUE_LOCK:
            state = _read_queue_state()
            if _recover_staged_batches(state):
                _write_queue_state(state)
            resolved = state["resolved"].get(batch_id)
            if resolved:
                if resolved.get("action") == action and action in ("reject", "complete"):
                    self.send_json(
                        200,
                        {
                            "ok": True,
                            "action": action,
                            "batch_id": batch_id,
                            "status": resolved["status"],
                        },
                    )
                    return
                raise QueueAPIError(409, "BATCH_ALREADY_RESOLVED", "The batch has already been resolved")

            pending = next(
                (batch for batch in state["pending"] if batch.get("batch_id") == batch_id),
                None,
            )
            approved = next(
                (batch for batch in state["approved"] if batch.get("batch_id") == batch_id),
                None,
            )
            if action == "ack_notification":
                if not approved or approved.get("status") != "approved":
                    raise QueueAPIError(404, "BATCH_NOT_FOUND", "The approved batch was not found")
                failures = approved.get("delivery_failures", [])
                matched = [
                    failure
                    for failure in failures
                    if failure.get("notification_id") in notification_ids
                ]
                if len(matched) != len(notification_ids):
                    raise QueueAPIError(
                        404, "NOTIFICATION_NOT_FOUND", "A failure notification was not found"
                    )
                for failure in matched:
                    failure["notified_at"] = failure.get("notified_at") or _utc_now()
                _write_queue_state(state)
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "action": action,
                        "batch_id": batch_id,
                        "status": "acknowledged",
                        "notification_ids": notification_ids,
                    },
                )
                return
            if action in ("approve", "reject"):
                if approved and action == "approve":
                    if approved.get("decision_chat_id") == TELEGRAM_DECISION_CHAT_ID:
                        result_status = "approved"
                    else:
                        raise QueueAPIError(409, "INVALID_BATCH_STATE", "The batch cannot be approved")
                else:
                    pending_batches = sorted(
                        (
                            batch
                            for batch in state["pending"]
                            if batch.get("status") == "pending"
                        ),
                        key=lambda batch: batch.get("created_at", ""),
                    )
                    if not pending or pending.get("status") != "pending":
                        raise QueueAPIError(404, "BATCH_NOT_FOUND", "The pending batch was not found")
                    if not pending_batches or pending_batches[0]["batch_id"] != batch_id:
                        raise QueueAPIError(
                            409, "NOT_OLDEST_PENDING", "Only the oldest pending batch may be resolved"
                        )
                    if action == "approve":
                        pending["status"] = "approved"
                        pending["decision_at"] = _utc_now()
                        pending["decision_chat_id"] = TELEGRAM_DECISION_CHAT_ID
                        pending["delivery_status"] = "ready"
                        for item in pending["items"]:
                            item["delivery"] = {
                                "email": "not_started",
                                "google_drive": "not_started",
                                "notebooklm": "not_started",
                            }
                        state["pending"].remove(pending)
                        state["approved"].append(pending)
                        _write_queue_state(state)
                        result_status = "approved"
                    else:
                        pending["status"] = "rejecting"
                        _write_queue_state(state)
                        if _recover_staged_batches(state):
                            _write_queue_state(state)
                        result_status = "rejected"
                self.send_json(
                    200,
                    {"ok": True, "action": action, "batch_id": batch_id, "status": result_status},
                )
                return

            if action == "record_step":
                batch = approved
                if not batch or batch.get("status") != "approved":
                    raise QueueAPIError(404, "BATCH_NOT_FOUND", "The approved batch was not found")
                step_order = ("email", "google_drive", "notebooklm")
                step_index = step_order.index(step)
                previous_step = step_order[step_index - 1] if step_index else None
                if previous_step and any(
                    item.get("delivery", {}).get(previous_step) != "succeeded"
                    for item in batch["items"]
                ):
                    raise QueueAPIError(
                        409, "DELIVERY_ORDER", "The preceding delivery step must succeed first"
                    )

                current_states = [
                    item.setdefault(
                        "delivery",
                        {
                            "email": "not_started",
                            "google_drive": "not_started",
                            "notebooklm": "not_started",
                        },
                    ).get(step, "not_started")
                    for item in batch["items"]
                ]
                if outcome == "started":
                    if any(current == "uncertain" for current in current_states):
                        raise QueueAPIError(
                            409, "RECONCILIATION_REQUIRED", "An uncertain step cannot be replayed"
                        )
                    if len(set(current_states)) > 1:
                        raise QueueAPIError(
                            409, "INVALID_STEP_STATE", "Batch delivery states are inconsistent"
                        )
                    if any(current == "succeeded" for current in current_states):
                        result_status = "approved"
                    else:
                        for item in batch["items"]:
                            item["delivery"][step] = "started"
                        result_status = "approved"
                elif outcome == "uncertain":
                    if any(current not in ("started", "uncertain") for current in current_states):
                        raise QueueAPIError(
                            409, "INVALID_STEP_TRANSITION", "Only a started step may become uncertain"
                        )
                    for item in batch["items"]:
                        item["delivery"][step] = "uncertain"
                    batch["delivery_status"] = "needs_reconciliation"
                    if "failure" in payload:
                        approved_titles = {
                            item.get("title") for item in batch.get("items", [])
                        }
                        reported_items = []
                        seen_items = set()
                        for reported in failure["items"]:
                            title = reported["title"].strip()
                            if title not in approved_titles:
                                raise QueueAPIError(
                                    422,
                                    "INVALID_FAILURE_ITEM",
                                    "Failure report title is not in the approved batch",
                                )
                            if title in seen_items:
                                continue
                            seen_items.add(title)
                            entry = {"title": title}
                            if reported.get("drive_file_id"):
                                entry["drive_file_id"] = reported["drive_file_id"].strip()
                            reported_items.append(entry)
                        message = re.sub(
                            r"(?i)\b(bearer\s+)[^\s,;]+",
                            r"\1[redacted]",
                            " ".join(failure["message"].split()),
                        )
                        message = re.sub(
                            r"(?i)\b(cookie|authorization|token|password|secret)(\s*[:=]\s*)[^\s,;]+",
                            r"\1\2[redacted]",
                            message,
                        )[:1000]
                        batch.setdefault("delivery_failures", []).append(
                            {
                                "notification_id": uuid.uuid4().hex,
                                "step": step,
                                "message": message,
                                "items": reported_items,
                                "created_at": _utc_now(),
                                "notified_at": None,
                            }
                        )
                    result_status = "needs_reconciliation"
                elif outcome == "not_started":
                    if any(current not in ("started", "uncertain", "not_started") for current in current_states):
                        raise QueueAPIError(
                            409, "INVALID_STEP_TRANSITION", "A completed step cannot be reset"
                        )
                    for item in batch["items"]:
                        item["delivery"][step] = "not_started"
                    batch["delivery_status"] = "in_progress"
                    result_status = "approved"
                else:
                    if caller_role == "operator" and any(
                        current != "uncertain" for current in current_states
                    ):
                        raise QueueAPIError(
                            403, "OPERATOR_REQUIRED", "Operators may only reconcile uncertain steps"
                        )
                    if any(current not in ("started", "succeeded", "uncertain") for current in current_states):
                        raise QueueAPIError(
                            409, "INVALID_STEP_TRANSITION", "A step must be started before it succeeds"
                        )
                    if any(current == "uncertain" for current in current_states) and caller_role != "operator":
                        raise QueueAPIError(
                            403, "OPERATOR_REQUIRED", "An operator must reconcile an uncertain step"
                        )
                    for item in batch["items"]:
                        item["delivery"][step] = "succeeded"
                    batch["delivery_status"] = "in_progress"
                    result_status = "approved"
                _write_queue_state(state)
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "action": action,
                        "batch_id": batch_id,
                        "status": result_status,
                    },
                )
                return

            if action == "complete":
                batch = approved
                if not batch or batch.get("status") != "approved":
                    raise QueueAPIError(404, "BATCH_NOT_FOUND", "The approved batch was not found")
                required_steps = ("email", "google_drive", "notebooklm")
                if any(
                    any(item.get("delivery", {}).get(step) != "succeeded" for item in batch["items"])
                    for step in required_steps
                ):
                    raise QueueAPIError(
                        409, "DELIVERY_INCOMPLETE", "All delivery steps must succeed before completion"
                    )
                batch["status"] = "completing"
                _write_queue_state(state)
                if _recover_staged_batches(state):
                    _write_queue_state(state)
                self.send_json(
                    200,
                    {"ok": True, "action": action, "batch_id": batch_id, "status": "complete"},
                )
                return

    def _handle_queue_request(self, path):
        try:
            if urlsplit(self.path).query:
                raise QueueAPIError(400, "INVALID_QUERY", "Queue mutation routes do not accept query parameters")
            if path == "/api/queue/add":
                return self._queue_add()
            if path == "/api/queue/resolve":
                return self._queue_resolve()
        except QueueAPIError as error:
            self._send_queue_error(error)
        except QueueStateError:
            self.send_json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "QUEUE_STATE_INVALID",
                        "message": "Queue state is unreadable or has an invalid schema",
                    },
                },
            )
        except Exception as error:
            print("Queue mutation failed:", repr(error))
            self.send_json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "Queue mutation failed",
                    },
                },
            )

    def do_POST(self):
        parsed_path = urlsplit(self.path)
        if parsed_path.path == "/api/queue/list":
            self.send_error(405)
            return
        if parsed_path.path in ("/api/queue/add", "/api/queue/resolve"):
            self._handle_queue_request(parsed_path.path)
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        # ------------------------------------------------------------------
        # 1. Filtro Duplo Inteligente (Data Guard + Catalog Guard)
        # ------------------------------------------------------------------
        if self.path == "/api/catalog/filter-new":
            try:
                payload = json.loads(body)
                items = payload.get("items", [])
                max_days_old = int(payload.get("max_days_old", 5))
                
                # Data de referência (default hoje)
                today = date.today()
                
                catalog = load_catalog()
                catalog_keys = {s.get("key") for s in catalog.get("sources", []) if s.get("key")}
                
                accepted = []
                rejected = []

                for item in items:
                    title = item.get("title", "")
                    desc = item.get("desc", "")
                    href = item.get("href", "")
                    
                    full_text = f"{title} {desc}"
                    dates, years = extract_dates(full_text)
                    
                    # Camada 1: Data Guard (Filtro Temporal de Recência)
                    if dates:
                        latest_date = max(dates)
                        days_diff = (today - latest_date).days
                        # Se a data de publicação for anterior à janela de recência (ex: mais de 5 dias atrás)
                        if days_diff > max_days_old:
                            rejected.append({
                                "title": title,
                                "reason": f"data_antiga ({latest_date.strftime('%d/%m/%Y')}, {days_diff} dias atras)"
                            })
                            continue
                    elif years and max(years) < today.year:
                        rejected.append({
                            "title": title,
                            "reason": f"ano_antigo ({max(years)})"
                        })
                        continue
                    else:
                        # Documentos normativos ou tabelas novas da Receita SEMPRE possuem data de publicação.
                        # Itens perenes sem data (ex: links institucionais fixos) são ignorados.
                        rejected.append({
                            "title": title,
                            "reason": "sem_data_de_publicacao"
                        })
                        continue

                    # Camada 2: Catalog Guard
                    key = make_key(title)
                    if key in catalog_keys:
                        rejected.append({
                            "title": title,
                            "reason": "ja_existe_no_catalogo"
                        })
                        continue

                    # Verificação secundária por código de norma (ex: NT 2026.008 v1.01)
                    num_match = re.search(r'(?:Nota\s*T[eé]cnica|NT|Informe\s*T[eé]cnico|IT|PL_)\s*([0-9._\-vVa-zA-Z]+)', title, re.I)
                    if num_match:
                        code = num_match.group(1).lower().replace('_', '').replace('.', '')
                        key_pattern = None
                        # NT/IT com versao: so e duplicata se tipo, numero e versao coincidirem
                        versioned = re.search(
                            r'(Nota\s*T[eé]cnica|NT|Informe\s*T[eé]cnico|IT)\s*[_\-]?\s*(\d{4})[.\-_]?(\d{3})\s*[_\-]?\s*v\s*(\d+)[.,](\d+)',
                            title, re.I)
                        if versioned:
                            kind = 'it' if versioned.group(1).lower().startswith('i') else 'nt'
                            code = f"{kind}{versioned.group(2)}{versioned.group(3)}v{versioned.group(4)}{versioned.group(5)}"
                            key_pattern = re.compile(r'(?:nfe)?' + re.escape(code))
                        already_in = False
                        for s in catalog.get("sources", []):
                            ckey = s.get("key", "")
                            if (key_pattern.search(ckey) if key_pattern else code in ckey):
                                already_in = True
                                break
                        if already_in:
                            rejected.append({
                                "title": title,
                                "reason": f"codigo_identificador_ja_no_catalogo ({code})"
                            })
                            continue

                    # Passou em todas as travas!
                    accepted.append(item)

                # Camada 3: Circuit Breaker
                circuit_breaker = len(accepted) > 10

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "count": len(accepted),
                    "items": accepted,
                    "rejected_count": len(rejected),
                    "rejected": rejected,
                    "circuit_breaker": circuit_breaker
                }, ensure_ascii=False).encode('utf-8'))
                return

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))
                return

        # ------------------------------------------------------------------
        # 2. Registrar Documento Processado no Catálogo Local
        # ------------------------------------------------------------------
        elif self.path == "/api/catalog/register":
            try:
                payload = json.loads(body)
                titles = payload.get("titles", [])
                if isinstance(titles, str):
                    titles = [titles]

                catalog = load_catalog()
                catalog_keys = {s.get("key") for s in catalog.get("sources", []) if s.get("key")}
                added = []

                for t in titles:
                    if not t:
                        continue
                    clean = normalize_title(t)
                    key = make_key(clean)
                    if key and key not in catalog_keys:
                        entry = {
                            "clean_title": clean,
                            "original_title": t,
                            "key": key,
                            "source_id": None,
                            "registered_at": datetime.utcnow().isoformat() + "Z"
                        }
                        catalog.setdefault("sources", []).append(entry)
                        catalog_keys.add(key)
                        added.append(clean)

                if added:
                    save_catalog(catalog)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "added_count": len(added),
                    "added": added,
                    "total_sources": len(catalog.get("sources", []))
                }, ensure_ascii=False).encode('utf-8'))
                return

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))
                return

        # ------------------------------------------------------------------
        # 3. Sincronizar Google Drive no NotebookLM
        # ------------------------------------------------------------------
        elif self.path == "/api/sync-notebooklm":
            try:
                data = json.loads(body)
                drive_file_id = data.get("drive_file_id") or data.get("id") or ""
                title = (
                    data.get("name")
                    or data.get("title")
                    or data.get("document_title")
                    or "Documento NFe"
                )
                mime_type = data.get("mime_type") or data.get("mimeType") or "pdf"

                if drive_file_id:
                    res = subprocess.run([SCRIPT_DRIVE, drive_file_id, title, mime_type], capture_output=True, text=True, timeout=30)
                    if res.returncode == 0:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "ok",
                            "drive_file_id": drive_file_id,
                            "mime_type": mime_type,
                            "output": res.stdout.strip()
                        }).encode('utf-8'))
                        return
                    else:
                        self.send_response(500)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "title": title,
                            "drive_file_id": drive_file_id,
                            "error": res.stderr.strip() or res.stdout.strip()
                        }).encode('utf-8'))
                        return

                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": "drive_file_id required"}).encode('utf-8'))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error",
                    "title": locals().get("title", "Documento NFe"),
                    "drive_file_id": locals().get("drive_file_id", ""),
                    "error": str(e)
                }).encode('utf-8'))
                return

        # ------------------------------------------------------------------
        # 4. Processar Schemas XML (.xsd) de pacotes ZIP
        # ------------------------------------------------------------------
        elif self.path == "/api/sync-zip-schemas":
            try:
                data = json.loads(body)
                zip_path = data.get("zip_path") or ""
                zip_url = data.get("url") or ""
                pkg_name = data.get("name") or "Pacote_Schemas.zip"
                
                if not zip_path or not os.path.exists(zip_path):
                    import urllib.request
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    zip_path = os.path.join(CACHE_DIR, pkg_name)
                    if zip_url:
                        headers = {'User-Agent': 'Mozilla/5.0', 'Cookie': 'AspxAutoDetectCookieSupport=1'}
                        req = urllib.request.Request(zip_url, headers=headers)
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            with open(zip_path, "wb") as f:
                                f.write(resp.read())

                if not os.path.exists(zip_path):
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "error": f"Arquivo ZIP não encontrado: {zip_path}"}).encode('utf-8'))
                    return

                list_res = subprocess.run([CLI_PATH, "source", "list", "-n", NOTEBOOK_ID, "--json"], capture_output=True, text=True, timeout=30)
                sources = []
                if list_res.returncode == 0:
                    parsed_list = json.loads(list_res.stdout)
                    sources = parsed_list.get("sources", []) if isinstance(parsed_list, dict) else parsed_list

                processed_xsds = []
                with zipfile.ZipFile(zip_path, 'r') as z:
                    for filename in z.namelist():
                        if filename.lower().endswith('.xsd'):
                            base_name = os.path.basename(filename)
                            md_title = os.path.splitext(base_name)[0] + ".md"
                            
                            xsd_content = z.read(filename).decode('utf-8', errors='ignore')
                            md_content = f"# Schema XML: {base_name}\n**Pacote:** {pkg_name}\n**Arquivo Original:** `{filename}`\n\n```xml\n{xsd_content}\n```\n"
                            
                            md_file_path = os.path.join(CACHE_DIR, md_title)
                            with open(md_file_path, "w", encoding="utf-8") as f:
                                f.write(md_content)

                            for src in sources:
                                cur_title = src.get("title", "")
                                if cur_title == md_title and not cur_title.startswith("[Z -Duplicadas Red]"):
                                    superseded_title = f"[Z -Duplicadas Red] {cur_title}"
                                    subprocess.run([CLI_PATH, "source", "rename", src["id"], superseded_title, "-n", NOTEBOOK_ID], capture_output=True, text=True, timeout=30)
                                    src["title"] = superseded_title

                            add_res = subprocess.run([CLI_PATH, "source", "add", md_file_path, "--title", md_title, "-n", NOTEBOOK_ID], capture_output=True, text=True, timeout=30)
                            processed_xsds.append({
                                "xsd": base_name,
                                "md_title": md_title,
                                "status": "added" if add_res.returncode == 0 else "error"
                            })

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "package": pkg_name,
                    "processed_count": len(processed_xsds),
                    "schemas": processed_xsds
                }).encode('utf-8'))
                return

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))
                return

        # ------------------------------------------------------------------
        # 5. Renomear Fontes Superadas no NotebookLM
        # ------------------------------------------------------------------
        elif self.path == "/api/rename-superseded":
            try:
                data = json.loads(body)
                removed_list = data.get("removed", [])
                prefix = data.get("prefix", "[Z -Duplicadas Red] ")
                
                if not removed_list:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok", "renamed": []}).encode('utf-8'))
                    return

                list_res = subprocess.run([CLI_PATH, "source", "list", "-n", NOTEBOOK_ID, "--json"], capture_output=True, text=True, timeout=30)
                if list_res.returncode != 0:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "error": list_res.stderr}).encode('utf-8'))
                    return
                
                parsed_list = json.loads(list_res.stdout)
                sources = parsed_list.get("sources", []) if isinstance(parsed_list, dict) else parsed_list
                
                renamed_results = []
                for rem in removed_list:
                    clean_rem = rem.replace("Nota Técnica", "").replace("NT", "").replace("Informe Técnico", "").replace("IT", "").strip()
                    tokens = [t for t in clean_rem.split() if len(t) > 2][:2]
                    
                    for src in sources:
                        cur_title = src.get("title", "")
                        if cur_title.startswith(prefix.strip()):
                            continue
                        
                        match = False
                        if all(tok in cur_title for tok in tokens):
                            match = True
                        
                        if match:
                            new_title = f"{prefix}{cur_title}"
                            ren_res = subprocess.run([CLI_PATH, "source", "rename", src["id"], new_title, "-n", NOTEBOOK_ID], capture_output=True, text=True, timeout=30)
                            renamed_results.append({
                                "id": src["id"],
                                "old_title": cur_title,
                                "new_title": new_title,
                                "success": ren_res.returncode == 0
                            })
                            src["title"] = new_title

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "renamed": renamed_results}).encode('utf-8'))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(e)}).encode('utf-8'))
                return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    recover_queue_state()
    server = HTTPServer((BIND_HOST, PORT), Handler)
    server.serve_forever()
