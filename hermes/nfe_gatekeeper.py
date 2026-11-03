import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import dotenv_values

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt


DEFAULT_ENV_FILE = Path("/opt/data/.env")
DEFAULT_STATE_FILE = Path("/opt/data/state/nfe_gatekeeper_notifications.json")
DEFAULT_DECISION_CHAT_ID = int(os.environ.get("TELEGRAM_DECISION_CHAT_ID", "123456789"))
DEFAULT_TELEGRAM_TARGET = os.environ.get("TELEGRAM_TARGET", f"telegram:{DEFAULT_DECISION_CHAT_ID}")
DEFAULT_SEND_COMMAND = ("/opt/hermes/bin/hermes", "send", "--json")
HTTP_TIMEOUT = 35
SEND_TIMEOUT = 45


class GatekeeperError(Exception):
    pass


def load_settings(env_file=DEFAULT_ENV_FILE):
    values = dotenv_values(env_file)
    required = (
        "NFE_DAEMON_INTERNAL_BASE_URL",
        "NFE_N8N_INTERNAL_BASE_URL",
        "QUEUE_HERMES_TOKEN",
        "NFE_HERMES_FLUX_B_TOKEN",
    )
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise GatekeeperError(
            "Required Hermes settings are missing: " + ", ".join(missing)
        )
    trigger_setting = values.get("NFE_ENABLE_FLUX_B_TRIGGER", "false").strip().lower()
    if trigger_setting not in ("true", "false"):
        raise GatekeeperError("NFE_ENABLE_FLUX_B_TRIGGER must be true or false")
    daemon = validate_internal_url(values["NFE_DAEMON_INTERNAL_BASE_URL"])
    n8n = validate_internal_url(values["NFE_N8N_INTERNAL_BASE_URL"])
    return {
        "daemon_url": daemon,
        "n8n_url": n8n,
        "queue_token": values["QUEUE_HERMES_TOKEN"],
        "webhook_token": values["NFE_HERMES_FLUX_B_TOKEN"],
        "trigger_enabled": trigger_setting == "true",
    }


def validate_internal_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise GatekeeperError("Service URL must be an internal HTTP base URL")
    hostname = parsed.hostname.lower()
    if hostname in ("localhost", "127.0.0.1", "::1"):
        raise GatekeeperError("Service URL must use internal Docker service DNS")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if hostname not in {"n8n", "notebooklm_receiver", "hermes-agent"}:
            raise GatekeeperError("Service URL must use internal Docker service DNS")
    else:
        if not address.is_private:
            raise GatekeeperError("Service URL must use an internal Docker address")
    if parsed.path not in ("", "/"):
        raise GatekeeperError("Service URL must be a base URL without a path")
    return value.rstrip("/")


def request_json(url, token, payload=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data = None
    method = "GET"
    if payload is not None:
        method = "POST"
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=HTTP_TIMEOUT)
        with response:
            raw = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as error:
        raw = error.read()
        status = error.code
        content_type = error.headers.get("Content-Type", "")
    except (OSError, URLError, TimeoutError) as error:
        raise GatekeeperError("Internal service request failed") from error
    if not content_type.lower().startswith("application/json"):
        raise GatekeeperError("Internal service returned a non-JSON response")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GatekeeperError("Internal service returned invalid JSON") from error
    if status < 200 or status >= 300 or not isinstance(result, dict):
        raise GatekeeperError("Internal service rejected the request")
    return result


def format_approval_message(batch):
    batch_id = batch.get("batch_id")
    items = batch.get("items")
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise GatekeeperError("Pending batch has no valid batch_id")
    if not isinstance(items, list) or not items:
        raise GatekeeperError("Pending batch contains no documents")
    lines = [
        "Novo lote SEFAZ para aprovacao manual",
        f"Lote: {batch_id}",
        "",
    ]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise GatekeeperError("Pending batch contains an invalid document")
        title = item.get("title")
        publication_date = item.get("date")
        if not isinstance(title, str) or not title.strip():
            raise GatekeeperError("Pending document has no valid title")
        if not isinstance(publication_date, str) or not publication_date.strip():
            raise GatekeeperError("Pending document has no valid date")
        lines.extend(
            (
                f"{index}. {title.strip()}",
                f"   Data: {publication_date.strip()}",
            )
        )
    lines.extend(
        (
            "",
            "Responda a esta mensagem com Aprovar ou Rejeitar.",
            "A decisao e valida somente para este lote.",
        )
    )
    return "\n".join(lines)


def send_telegram(message, *, target=DEFAULT_TELEGRAM_TARGET, send_command=None):
    command = tuple(send_command or DEFAULT_SEND_COMMAND)
    try:
        result = subprocess.run(
            (*command, "--to", target),
            input=message,
            capture_output=True,
            text=True,
            timeout=SEND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GatekeeperError("Hermes Telegram delivery failed") from error
    if result.returncode:
        raise GatekeeperError("Hermes Telegram delivery failed")
    try:
        acknowledgement = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GatekeeperError("Hermes returned an invalid Telegram acknowledgement") from error
    if (
        not isinstance(acknowledgement, dict)
        or acknowledgement.get("success") is not True
        or acknowledgement.get("skipped") is True
    ):
        raise GatekeeperError("Hermes did not confirm Telegram delivery")
    return acknowledgement


@contextmanager
def locked_state(path):
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        else:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(" ")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise GatekeeperError("Notification state file is unreadable") from error
                if not isinstance(state, dict) or set(state) != {"last_notified_batch_id"}:
                    raise GatekeeperError("Notification state schema is invalid")
            else:
                state = {"last_notified_batch_id": None}
            yield state
            descriptor, temporary = tempfile.mkstemp(
                prefix=".nfe-notification-", suffix=".tmp", dir=state_path.parent
            )
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(state, output, ensure_ascii=False)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, state_path)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            else:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def pending_batch(settings):
    response = request_json(
        settings["daemon_url"] + "/api/queue/list?state=pending",
        settings["queue_token"],
    )
    batches = response.get("batches")
    if response.get("ok") is not True or not isinstance(batches, list):
        raise GatekeeperError("Daemon returned an invalid pending queue")
    if not batches:
        return None
    batch = batches[0]
    if not isinstance(batch, dict):
        raise GatekeeperError("Daemon returned an invalid pending batch")
    return batch


def pending_failure_notifications(settings):
    response = request_json(
        settings["daemon_url"] + "/api/queue/list?state=notifications",
        settings["queue_token"],
    )
    notifications = response.get("notifications")
    if response.get("ok") is not True or not isinstance(notifications, list):
        raise GatekeeperError("Daemon returned invalid failure notifications")
    for notification in notifications:
        if (
            not isinstance(notification, dict)
            or not isinstance(notification.get("notification_id"), str)
            or not isinstance(notification.get("batch_id"), str)
            or notification.get("step") != "notebooklm"
            or not isinstance(notification.get("message"), str)
            or not isinstance(notification.get("items"), list)
            or not notification["items"]
        ):
            raise GatekeeperError("Daemon returned an invalid failure notification")
        for item in notification["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("title"), str):
                raise GatekeeperError("Daemon returned invalid failed document details")
    return notifications


def format_failure_message(notification):
    lines = [
        "Falha ao sincronizar documentos no NotebookLM",
        f"Lote: {notification['batch_id']}",
        "",
        "Arquivos que precisam de atenção:",
    ]
    for item in notification["items"]:
        drive_id = item.get("drive_file_id")
        suffix = f" (ID no Drive: {drive_id})" if drive_id else ""
        lines.append(f"- {item['title']}{suffix}")
    lines.extend(
        (
            "",
            "Erro reportado:",
            notification["message"],
            "",
            "A etapa do Google Drive já havia sido concluída; os arquivos continuam lá.",
            "O lote foi retido para reconciliação e não será repetido automaticamente.",
        )
    )
    return "\n".join(lines)


def acknowledge_failure_notification(settings, notification):
    result = request_json(
        settings["daemon_url"] + "/api/queue/resolve",
        settings["queue_token"],
        {
            "batch_id": notification["batch_id"],
            "action": "ack_notification",
            "notification_ids": [notification["notification_id"]],
        },
    )
    if (
        result.get("ok") is not True
        or result.get("status") != "acknowledged"
    ):
        raise GatekeeperError("Daemon did not confirm failure notification acknowledgement")


def poll_once(
    *,
    env_file=DEFAULT_ENV_FILE,
    state_file=DEFAULT_STATE_FILE,
    send_command=None,
):
    settings = load_settings(env_file)
    with locked_state(state_file) as state:
        notifications = pending_failure_notifications(settings)
        for notification in notifications:
            send_telegram(format_failure_message(notification), send_command=send_command)
            acknowledge_failure_notification(settings, notification)

        batch = pending_batch(settings)
        if batch is None:
            state["last_notified_batch_id"] = None
            return {
                "status": "failure_notified" if notifications else "empty",
                "failure_notification_count": len(notifications),
            }
        message = format_approval_message(batch)
        batch_id = batch["batch_id"]
        if state.get("last_notified_batch_id") == batch_id:
            return {
                "status": "already_notified",
                "batch_id": batch_id,
                "failure_notification_count": len(notifications),
            }
        send_telegram(message, send_command=send_command)
        state["last_notified_batch_id"] = batch_id
        return {
            "status": "notified",
            "batch_id": batch_id,
            "document_count": len(batch["items"]),
            "failure_notification_count": len(notifications),
        }


def _resolve_validated_decision(
    batch_id,
    action,
    *,
    env_file=DEFAULT_ENV_FILE,
):
    if action not in ("approve", "reject"):
        raise GatekeeperError("Decision must be approve or reject")
    if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", batch_id):
        raise GatekeeperError("Batch ID is invalid")
    settings = load_settings(env_file)
    batch = pending_batch(settings)
    if batch is None or batch.get("batch_id") != batch_id:
        raise GatekeeperError("Decision does not match the oldest pending batch")
    decision = request_json(
        settings["daemon_url"] + "/api/queue/resolve",
        settings["queue_token"],
        {"batch_id": batch_id, "action": action, "chat_id": DEFAULT_DECISION_CHAT_ID},
    )
    if decision.get("ok") is not True or decision.get("status") != (
        "approved" if action == "approve" else "rejected"
    ):
        raise GatekeeperError("Daemon did not confirm the requested decision")
    result = {"status": decision["status"], "batch_id": batch_id}
    if action == "approve":
        if not settings.get("trigger_enabled", False):
            result["distribution"] = "stubbed"
            return result
        trigger = request_json(
            settings["n8n_url"] + "/webhook/nfe-aprovado-interno",
            settings["webhook_token"],
            {"batch_id": batch_id},
        )
        if (
            trigger.get("ok") is not True
            or trigger.get("batch_id") != batch_id
        ):
            raise GatekeeperError(
                "Flow B trigger was not confirmed; the approved batch remains recoverable"
            )
        result["distribution"] = trigger.get("status", "accepted")
    return result


def decide_message(
    batch_id,
    chat_id,
    reply_to_batch_id,
    text,
    *,
    env_file=DEFAULT_ENV_FILE,
):
    if isinstance(chat_id, bool) or chat_id != DEFAULT_DECISION_CHAT_ID:
        raise GatekeeperError("Decision sender is not authorized")
    if not isinstance(batch_id, str) or reply_to_batch_id != batch_id:
        raise GatekeeperError("Decision is not a reply to the current batch")
    if not isinstance(text, str):
        raise GatekeeperError("Decision text is invalid")
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", text.strip().casefold())
        if not unicodedata.combining(character)
    )
    action = {
        "aprovar": "approve",
        "rejeitar": "reject",
    }.get(normalized)
    if action is None:
        raise GatekeeperError("Decision text must be Aprovar or Rejeitar")
    return _resolve_validated_decision(batch_id, action, env_file=env_file)


def test_message():
    items = (
        ("DOCUMENTO SEFAZ SIMULADO A - HOMOLOGACAO", date(2026, 10, 30)),
        ("DOCUMENTO SEFAZ SIMULADO B - HOMOLOGACAO", date(2026, 10, 31)),
    )
    lines = [
        "TESTE DE DISPARO DO GATEKEEPER - NAO RESPONDA",
        "Este e um teste com lote ficticio; nao ha aprovacao pendente.",
        "Nenhuma distribuicao sera executada.",
        "",
    ]
    for index, (title, publication_date) in enumerate(items, start=1):
        lines.extend(
            (
                f"{index}. {title}",
                f"   Data: {publication_date.isoformat()}",
            )
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hermes SEFAZ approval Gatekeeper")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    poll = subparsers.add_parser("poll-once")
    poll.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    poll.add_argument("--quiet", action="store_true")
    message_decision = subparsers.add_parser("decide-message")
    message_decision.add_argument("batch_id")
    message_decision.add_argument("--chat-id", required=True, type=int)
    message_decision.add_argument("--reply-to-batch-id", required=True)
    message_decision.add_argument("--text", required=True)
    subparsers.add_parser("test-send")
    args = parser.parse_args(argv)
    try:
        if args.command == "poll-once":
            result = poll_once(
                env_file=args.env_file,
                state_file=args.state_file,
            )
        elif args.command == "decide-message":
            result = decide_message(
                args.batch_id,
                args.chat_id,
                args.reply_to_batch_id,
                args.text,
                env_file=args.env_file,
            )
        else:
            result = send_telegram(test_message())
            result = {"status": "test_sent"}
        if not getattr(args, "quiet", False):
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except GatekeeperError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
