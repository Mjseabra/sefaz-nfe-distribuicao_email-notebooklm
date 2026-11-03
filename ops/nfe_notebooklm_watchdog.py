#!/usr/bin/env python3
"""Keeps the NotebookLM session alive and self-heals batches held after an auth failure."""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

CLI = os.environ.get("NOTEBOOKLM_CLI", "/home/ubuntu/.venvs/notebooklm/bin/notebooklm")
NOTEBOOK_ID = os.environ.get("NOTEBOOKLM_ID", "00934ae7-9818-4fa4-a698-2249cd5270f9")
DAEMON = os.environ.get("NFE_DAEMON_URL", "http://172.19.0.1:8555")
STATE_FILE = Path(os.environ.get("WATCHDOG_STATE", "/home/ubuntu/.nfe-notebooklm-watchdog.json"))
TELEGRAM_TARGET = os.environ.get("TELEGRAM_TARGET", "telegram:123456789")
HERMES_SEND = ("docker", "exec", "-i", "hermes-agent", "/opt/hermes/bin/hermes", "send", "--json", "--to", TELEGRAM_TARGET)
RUN_TIMEOUT = 300


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=RUN_TIMEOUT)


def telegram(message):
    try:
        subprocess.run(HERMES_SEND, input=message, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        print("telegram delivery failed", file=sys.stderr)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"auth_ok": True}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state))


def daemon(path, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        DAEMON + path,
        data=data,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def existing_drive_ids():
    result = run([CLI, "source", "list", "-n", NOTEBOOK_ID, "--json"])
    if result.returncode:
        raise RuntimeError("source list failed")
    return {s.get("drive_document_id") for s in json.loads(result.stdout).get("sources", [])}


def reconcile(flux_b, operator):
    batches = daemon("/api/queue/list?state=approved", flux_b).get("batches", [])
    for batch in batches:
        if batch.get("delivery_status") != "needs_reconciliation":
            continue
        items = batch.get("items", [])
        states = {i.get("delivery", {}).get("notebooklm") for i in items}
        if states != {"uncertain"}:
            continue
        if any(i.get("delivery", {}).get(s) != "succeeded" for i in items for s in ("email", "google_drive")):
            continue
        drive_ids = {}
        for failure in batch.get("delivery_failures", []):
            for entry in failure.get("items", []):
                if entry.get("drive_file_id"):
                    drive_ids[entry["title"]] = entry["drive_file_id"]
        if any(i.get("title") not in drive_ids for i in items):
            continue
        present = existing_drive_ids()
        for item in items:
            file_id = drive_ids[item["title"]]
            if file_id in present:
                continue
            added = run([CLI, "source", "add-drive", file_id, item["title"], "--mime-type", "pdf", "-n", NOTEBOOK_ID])
            if added.returncode:
                raise RuntimeError("add-drive failed for " + item["title"])
        daemon(
            "/api/queue/resolve",
            operator,
            {"batch_id": batch["batch_id"], "action": "record_step", "step": "notebooklm", "outcome": "succeeded"},
        )
        daemon("/api/queue/resolve", flux_b, {"batch_id": batch["batch_id"], "action": "complete"})
        telegram(
            "O login do NotebookLM voltou e o lote %s foi concluído automaticamente "
            "(%d documento(s) vinculados ao caderno)." % (batch["batch_id"], len(items))
        )


def main():
    state = load_state()
    refreshed = run([CLI, "auth", "refresh", "--verify"])
    if refreshed.returncode:
        if state.get("auth_ok", True):
            telegram(
                "Atenção: o login do NotebookLM expirou e a renovação automática falhou.\n"
                "Os e-mails e o Drive continuam funcionando; só o vínculo com o NotebookLM está parado.\n"
                "Quando o login for renovado, o sistema conclui sozinho os lotes pendentes."
            )
        state["auth_ok"] = False
        save_state(state)
        return 1
    if not state.get("auth_ok", True):
        telegram("O login do NotebookLM foi restabelecido.")
    state["auth_ok"] = True
    save_state(state)
    flux_b = os.environ.get("QUEUE_FLOW_B_TOKEN", "")
    operator = os.environ.get("QUEUE_OPERATOR_TOKEN", "")
    if flux_b and operator:
        try:
            reconcile(flux_b, operator)
        except Exception as error:
            print("reconcile failed:", repr(error), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
