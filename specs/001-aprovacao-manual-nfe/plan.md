# Implementation Plan: Aprovação Manual de Notificações SEFAZ NFe

**Branch**: `001-aprovacao-manual-nfe` | **Date**: 2026-10-31 | **Spec**:
[spec.md](./spec.md)

**Input**: Feature specification from
`specs/001-aprovacao-manual-nfe/spec.md` and the user-provided technical architecture.

## Summary

This is a brownfield change to the supplied system, not a greenfield implementation.
Extend the existing `notebooklm_receiver.py` on port 8555 with only the three queue
routes (`/api/queue/add`, `/api/queue/list`, and `/api/queue/resolve`) and the minimal
supporting state logic they require. Keep the existing Data Guard, Catalog Guard,
catalog filtering behavior, and unrelated receiver routes intact; do not refactor or
replace them. Modify the supplied active n8n workflow JSON, `alerta_zen_atual.json`,
to introduce the human approval gate and separate collection from distribution while
preserving and reusing its existing processing logic where applicable. Hermes remains
the exclusive approval Gatekeeper, using outbound Telegram Long Polling for chat
`123456789`. Queue state remains in daemon-managed JSON, never n8n SQLite. Approved
batches remain recoverable until distribution succeeds. No new external port is
published, images must support native ARM64, and implementation/test email is fixed to
`operador@exemplo.com.br`.

## Technical Context

**Language/Version**: Existing Python 3 script using the standard-library
`http.server.HTTPServer` and `BaseHTTPRequestHandler`; retain its current runtime and
deployment rather than introducing a new receiver.

**Primary Dependencies**: Existing receiver and n8n workflow integrations with Hermes,
Telegram, ChangeDetection, Google Drive, and NotebookLM. Add no replacement HTTP
framework or duplicate receiver service.

**Storage**: Daemon-managed `fila_pendencias.json` for pending/approved batches and
distribution progress; preserve the existing `/home/ubuntu/data/notebooklm_catalog.json`
format and Catalog Guard behavior when recording rejected/completed documents. No
business state, queue, or rules in n8n SQLite.

**Testing**: Add focused route tests and workflow validation for the brownfield changes.
Verify existing filter/catalog scenarios remain unchanged. The root contains no
discovered automated test suite; use the project's current conventions if tests are
present in the deployment.

**Target Platform**: Linux Docker containers on ARM64 (Ampere A1), connected to the
user-defined private bridge `rede-automacao`. Service-to-service traffic uses this
network; required provider calls use outbound egress only. No new inbound firewall
exposure or published container port.

**Project Type**: Existing Python HTTP receiver and n8n automation workflow, extended
with queue/approval behavior and a Telegram approval agent.

**Performance Goals**: No throughput or latency target was provided. Preserve all
eligible documents and process batches in FIFO order; correctness, no loss, and no
unauthorized distribution take priority over throughput.

**Constraints**: All API responses are structured JSON; explicit timeouts of at least
30 seconds for HTTP requests and subprocesses; pending approvals do not expire; only
Telegram chat `123456789` may decide; no automatic corporate email before approval; test
email recipient is statically fixed to `operador@exemplo.com.br`; approved work remains
recoverable until distribution completes.

**Scale/Scope**: SEFAZ document batches, the two logical collection/distribution stages
derived from the supplied n8n workflow, the existing receiver, Hermes approval handling,
the existing catalog, and their deployment configuration. No scale estimate or
non-functional throughput target is available.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Result |
|---|---|---|
| Security-First and Human Approval | No corporate email or distribution without an explicit valid approval; enforce the test-recipient allowlist. | PASS |
| Exclusive Gatekeeper and Role Separation | Hermes alone accepts the human decision; Flux A only collects and Flux B only distributes daemon-approved batches. | PASS |
| Internal Network and State Isolation | Inter-service calls use `rede-automacao`; workflow state is daemon-managed JSON, never n8n SQLite. | PASS |
| Secure, ARM64-Compatible Infrastructure | No new public listener/port; Telegram uses outbound Long Polling; all images must support ARM64. | PASS |
| Bounded Execution and Structured APIs | HTTP and subprocess timeouts are at least 30 seconds; success and error API bodies are JSON. | PASS |

No constitutional exception is required. The deployed network configuration must be
checked before implementation: a Docker bridge marked `internal: true` blocks external
egress. Use `rede-automacao` as a user-defined bridge with no published container ports
and controlled outbound access. If the deployment requires `internal: true`, provider
egress must use an existing controlled gateway rather than another shared container
network.

## Project Structure

### Documentation (this feature)

```text
specs/001-aprovacao-manual-nfe/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── queue-api.yaml
│   └── distribution-trigger.md
└── tasks.md              # Created by /speckit-tasks, not by this command
```

### Existing Source and Workflow (repository root)

```text
notebooklm_receiver.py       # Existing receiver; add only the three queue routes
alerta_zen_atual.json        # Existing active 18-node "Alerta Zen com Assinatura MPL" flow to modify
/home/ubuntu/data/
  notebooklm_catalog.json    # Existing catalog; preserve schema and existing guards
  fila_pendencias.json       # New daemon-managed runtime queue; not committed
```

**Structure Decision**: Brownfield modification of the supplied receiver and active
workflow JSON. Keep the receiver's current HTTP server, Catalog Guard, Data Guard,
catalog schema, and unrelated routes unchanged; add the queue routes alongside the
existing handlers. Modify `alerta_zen_atual.json` to add the Hermes approval boundary
and split the existing process into collection and distribution stages, reusing current
nodes where possible instead of replacing the workflow with a new implementation.

## Complexity Tracking

No constitution violations or additional projects are proposed.

## Design Decisions and Implementation Gates

1. `/api/queue/resolve` approval moves a batch out of the **pending** list and into an
   **approved** state in `fila_pendencias.json`; it does not destroy the only copy of
   approved documents. This reconciles the requested "empty pending queue on approval"
   with Flux B's requirement to read those file paths. Flux B clears the approved state
   only after successful delivery.
2. The daemon serializes JSON mutations under a stable lock and commits each JSON update
   using same-directory temp-file replacement. Reject/complete handling stages the
   operation in queue state, idempotently updates the existing catalog format, and then
   removes the batch; startup/retry resumes staged operations. Do not migrate or
   rewrite the current catalog schema.
3. Hermes polls for the oldest pending batch, sends a grouped Telegram prompt containing
   its batch ID, and accepts only a reply to that prompt from chat `123456789`. At most
   one batch is awaiting a decision at a time; subsequent batches remain queued.
4. On approval, Hermes triggers the existing Flux B webhook using the internal
   distribution contract. It sends only a batch ID; Flux B must retrieve document
   paths from the daemon and verify approved state.
5. Flux B checks daemon state before distribution and records per-stage progress through
   the same resolution route. A known-completed stage is not repeated. An ambiguous
   external outcome (for example, connection loss while sending email) is held for
   reconciliation rather than automatically retried, because SMTP does not provide a
   universal exactly-once guarantee.
6. Preserve current receiver behavior: make no changes to the Data Guard, Catalog Guard,
   `/api/catalog/filter-new`, existing catalog format, or unrelated route handling.
   Add regression checks that prove the existing recency and duplicate-filter results
   are unchanged.
7. Treat `alerta_zen_atual.json` as the existing workflow to modify. Preserve its
   working collection and integration logic where applicable; add the approval gate and
   ensure distribution can only run for a daemon-confirmed approved batch.
8. Before implementation, confirm the existing Telegram/Hermes permissions, PDF
   staging path, current workflow webhook wiring, and Docker network egress. Adjust
   implementation details to those existing artifacts without broadening this feature
   into a rewrite.
9. NotebookLM failure reports are stored with the Daemon-managed approved batch and
   exposed to Hermes through the authenticated queue API. Hermes sends the report to
   chat `123456789` over the existing outbound Long Polling connector and acknowledges
   it only after Telegram confirms delivery; n8n must not send Telegram directly.
   Flux B must collect the outcomes for every NotebookLM item before recording batch
   success, so a mixed success/failure result cannot be presented as complete.

## Post-Design Constitution Check

| Principle | Design evidence | Result |
|---|---|---|
| Security-First and Human Approval | Hermes approval is required before any approved state; test recipient is fixed and checked. | PASS |
| Exclusive Gatekeeper and Role Separation | Hermes decides; n8n flows only collect/distribute; daemon validates transition state. | PASS |
| Internal Network and State Isolation | Queue/catalog are managed by the daemon; API calls use the internal Docker network; no n8n SQLite state. | PASS |
| Secure, ARM64-Compatible Infrastructure | Telegram uses outbound Long Polling; no new inbound ports; deployment validation checks every image manifest. | PASS |
| Bounded Execution and Structured APIs | Contracts require JSON success/errors; all external HTTP/subprocess operations have timeouts >=30 seconds. | PASS |

**Gate result**: PASS, subject to the brownfield compatibility checks above. No
constitutional violation remains. Implementation is limited to the requested additive
queue routes and modifications to the supplied workflow; it does not authorize replacing
the existing receiver or rewriting its established filtering logic.
