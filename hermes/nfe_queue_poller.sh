#!/bin/sh
set -eu
exec /opt/hermes/.venv/bin/python /opt/data/skills/nfe-gatekeeper/nfe_queue_poller.py
