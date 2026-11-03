# Quickstart: Validar Aprovação Manual NFe

This guide describes implementation validation, not production distribution. This
workspace contains the brownfield receiver `notebooklm_receiver.py` and the existing
n8n export `alerta_zen_atual.json`. Runtime checks against VPS `nx01` are recorded below.
Never use production recipient lists or real provider actions during tests.

## Confirmed local artifact details

- The existing receiver listens on port `8555`; its deployment defaults remain
  `CATALOG_PATH=/home/ubuntu/data/notebooklm_catalog.json` and
  `PDF_CACHE_DIR=/home/ubuntu/nfe-pdf-cache`. `QUEUE_PATH` defaults to
  `fila_pendencias.json` beside the catalog.
- The existing workflow export is UTF-16 JSON. Node `node-baixar-pdf` (“Baixar
  Arquivo”) currently connects to both “Agrupar Anexos para E-mail” and “Upload Google
  Drive”. The export contains the collector, SMTP, Drive and NotebookLM nodes; preserve
  this active workflow during later integration.
- Hermes, Docker networking, runtime secrets, and image manifests were inspected on
  VPS `nx01`; verified facts and remaining checks are recorded below.
- The queue API requires unique role-scoped bearer secrets injected by the deployment
  secret mechanism as `QUEUE_FLOW_A_TOKEN`, `QUEUE_HERMES_TOKEN`,
  `QUEUE_FLOW_B_TOKEN`, and `QUEUE_OPERATOR_TOKEN`. The receiver fails closed for an
  endpoint when its required credential is absent. Never store these values in this
  repository or queue/catalog JSON. `QUEUE_OPERATOR_TOKEN` is required only to reconcile
  uncertain delivery steps.
- `BIND_HOST`, `PORT`, `CATALOG_PATH`, `PDF_CACHE_DIR`, and `QUEUE_PATH` may be
  overridden for isolated local validation. Production defaults keep the existing
  port and bind behavior; local tests bind only to `127.0.0.1`.
- The modified workflow uses `NFE_HERMES_FLUX_B_TOKEN` for its internal webhook and
  `QUEUE_FLOW_A_TOKEN`/`QUEUE_FLOW_B_TOKEN` for daemon routes. Hermes needs
  `QUEUE_HERMES_TOKEN`, `NFE_HERMES_FLUX_B_TOKEN`, and
  `NFE_N8N_INTERNAL_BASE_URL` through its secret/configuration mechanism.
- The n8n and receiver use the same persistent PDF directory at
  `/home/ubuntu/nfe-pdf-cache`; the n8n file node writes PDFs there and the daemon
  validates/reads those same paths. The n8n mount and write access were verified on
  VPS `nx01`.
- Hermes system instructions and API/tool requirements are captured in
  `hermes-gatekeeper.md`. The active Telegram connector was observed connected in Long
  Polling mode, and its allowlist contains only chat `123456789`. An operational queue
  poller and Gatekeeper helper are installed. Validation of invalid replies and
  unauthorized senders through the active Telegram interaction remains pending.

## Prerequisites

- Locate the deployed `notebooklm_receiver.py`, `notebooklm_catalog.json`, n8n Flux A
  and Flux B exports, Hermes configuration, and Docker deployment files.
- Run all containers on `rede-automacao`; do not publish a new host port.
- Configure the daemon JSON volume and temporary PDF staging directory with restricted
  ownership and permissions.
- Load the daemon service credential and Telegram bot token from the existing secret
  mechanism. Do not put credentials in the repository or command history.
- Configure the Telegram decision chat as `123456789`.
- Fix the Flux B email recipient to exactly `operador@exemplo.com.br` in To; leave Cc and Bcc
  empty.
- Stub email, Google Drive, and NotebookLM integrations for automated tests. No real
  distribution should occur in test runs.

## 1. Validate artifacts and ARM64 images

From the deployed project directory, confirm each service image has a native ARM64
manifest. Replace `<image:tag>` with each image used by n8n, Hermes, and the receiver:

```sh
docker buildx imagetools inspect <image:tag>
```

Expected: every image advertises `linux/arm64`; emulation-only support does not pass.
Inspect the Compose/deployment configuration and verify all internal service URLs use
`rede-automacao`, no new `ports:` mapping or firewall ingress is introduced, and
required provider access is outbound only.

## 2. Run focused daemon tests

Run the focused standard-library tests from the project root:

```sh
python -m unittest discover -s tests -v
```

Expected coverage includes JSON request and error bodies, auth rejection, invalid/out
of-root file paths, five-day filtering inputs, catalog and queue deduplication, atomic
file updates, and concurrent add/resolve operations. Use temporary files and fake
provider integrations; do not touch the production catalog.

The test files are organized by responsibility:

- `tests/test_receiver_filter_regression.py`: legacy Data Guard and Catalog Guard
  regression behavior.
- `tests/test_receiver_queue.py`: isolated JSON persistence, atomic writes,
  concurrency, recovery, idempotency, and long-retention behavior.
- `tests/test_receiver_queue_api.py`: HTTP route contracts, authentication, and
  structured JSON responses.
- `tests/test_approval_e2e_stubbed.py`: isolated approval/rejection API flows with
  delivery effects stubbed and temporary queue/catalog/PDF storage.
- `tests/test_n8n_workflow.py`: workflow graph, approval gate, internal endpoints,
  distribution ordering, and fixed test recipient.

The 30-day retention assertion stores a pending batch in a temporary
`fila_pendencias.json`, reloads it, and checks its metadata and document survive while
the catalog file remains separate and untouched.

Local validation record (2026-10-31): `python -m unittest discover -s tests -v`
passed all 44 tests. The isolated approval/rejection harness uses temporary queue,
catalog, and PDF paths with stubbed delivery effects; it does not execute the deployed
n8n workflow or contact email, Google Drive, NotebookLM, or Telegram.

Remote validation record (2026-10-31, VPS `nx01`):

- The deployed receiver runs on Ubuntu `aarch64` with Python 3.12.3. Its systemd
  listener is restricted to `172.19.0.1:8555`; port 8555 is not published by Docker.
- Authenticated queue-list reads from the n8n and Hermes containers returned structured
  JSON. Unauthenticated reads from both paths returned structured JSON `401` responses.
  Queue credentials are stored in protected service/config files; their values are not
  recorded here.
- Hermes' persistent NFe skill and helper are installed under
  `/opt/data/skills/nfe-gatekeeper`. The Telegram connector uses Long Polling and the
  configured allowed chat is `123456789`.
- The `nfe-gatekeeper-poll` no-agent cron job is active at a one-minute interval and
  runs `/opt/data/scripts/nfe_queue_poller.sh`. After normalizing the wrapper to Unix
  line endings, a scheduled run completed successfully. The live pending queue was
  empty at the latest check, so no real approval prompt was sent.
- A single non-actionable synthetic test message containing two fake titles and dates
  was sent to chat `123456789`. Hermes CLI returned a successful send status; the
  helper initially expected an `ok` field instead of Hermes' `success` field, so its
  acknowledgement parser was corrected and tested. The message was not resent.
- `NFE_ENABLE_FLUX_B_TRIGGER=false` is set in the Hermes runtime while provider-stub
  validation is pending. An approval can be recorded, but the live helper will not
  call Flux B or cause real email, Drive, or NotebookLM effects.
- The active n8n, n8n sandbox runner/API, ChangeDetection, browser worker, and
  `hermes-agent` containers are attached only to `rede-automacao`. The persistent
  Compose entries for n8n, ChangeDetection, and the browser worker also specify only
  that network. The non-running, stale Compose service named `hermes` was reconciled
  from `app_network` to `rede-automacao`; its host port mapping was removed so starting
  the obsolete service cannot publish another port. The active `hermes-agent` container
  was not restarted or changed.
  `rede-automacao` is a user-defined bridge without Docker's `internal` option and
  retains the outbound access needed by Telegram and ChangeDetection. Active n8n,
  Hermes-agent, and ChangeDetection port mappings were unchanged; no new mapping was
  added to the active containers.
  UFW is inactive. The VPS provider firewall could not be inspected because no
  provider CLI or readable provider configuration was available.
- The PDF cache is mounted at the same path in n8n and the receiver. A write/read
  permission check from the n8n container passed. No PDF fixture was left behind.
- `docker buildx imagetools inspect` confirmed native `linux/arm64` manifests for
  `docker.n8n.io/n8nio/n8n:latest` and `nousresearch/hermes-agent:latest`. The receiver
  is a host Python service, not a Docker image; its runtime host reports `aarch64`.
- The active container images for n8n, Hermes, n8n sandbox runner/API,
  ChangeDetection, and the browser worker all report native `linux/arm64` manifests
  and were running as `linux/arm64` on the VPS. The host receiver runs on `aarch64`.
  Active published port bindings observed: n8n `5678`, ChangeDetection `5000`, and
  Hermes `9119`, each bound to `0.0.0.0` and IPv6 `::`. The sandbox runner/API and
  browser worker have no published host ports; the receiver is host-bound at
  `172.19.0.1:8555`. Separate OmniRoute services also expose `20128`; they are outside
  this feature's container inventory. These are observed existing bindings, not a
  provider-firewall inspection.
- Three isolated tests ran against the receiver module with temporary
  queue/catalog/PDF paths: approval followed by stubbed ordered delivery and
  completion, rejection followed by catalog-blocked resubmission, and injected
  ambiguous failures at email, Drive, and NotebookLM with operator reconciliation
  before retry. The failure test proves daemon state stays recoverable and disallows
  unsafe automatic replay; the isolated n8n error-branch execution is recorded below.
- The brownfield Flux B workflow was also executed in an ephemeral n8n container on
  `rede-automacao`, against an isolated receiver and temporary queue/catalog/PDF data.
  The pending batch did not reach provider nodes. The approved path ran email → Drive
  → NotebookLM in order; all six `record_step` calls and `complete` returned success,
  the email stub confirmed only `operador@exemplo.com.br`, the queue was cleared and the
  catalog updated, and a replay did not reach provider nodes. SMTP, Drive, and
  NotebookLM were stubs; no production state or real provider was touched. Resuming
  the full workflow after an interrupted provider remains outside this completed
  success-path run.
- An actual Telegram prompt for an isolated synthetic batch was sent to chat
  `123456789` through the Hermes Long Polling helper. The authorized user replied
  “Aprovar”, and the isolated daemon recorded the batch as approved. Flux B triggering
  was disabled; no email, Drive, or NotebookLM action occurred. The Hermes helper was
  temporarily routed to the isolated daemon after confirming the production queue was
  empty. The original Hermes environment and notification state were restored
  afterward, and the production queue remained empty. The real Telegram rejection
  test was subsequently completed using another isolated synthetic batch: the
  authorized user replied “Rejeitar”; the temporary daemon removed it from pending and
  recorded its title in the temporary catalog. A resubmission was rejected by
  `/api/catalog/filter-new`. Flux B remained disabled, no provider effect occurred, the
  Hermes configuration was restored, and production queue/catalog state was not
  changed. Real Telegram handling of invalid text and unauthorized senders remains
  unverified.

Still pending: validate invalid Telegram text and unauthorized senders through the
active Hermes/Long Polling interaction, and inspect the VPS provider firewall. No
unauthorized Telegram message was sent or simulated as a real third-party interaction.
Do not claim a fully validated production E2E until these checks pass.

Latest isolated validation record (2026-10-31):

- The full local suite passed all 44 tests; `git diff --check` reported no whitespace
  errors.
- Flux B's three provider error branches were executed in an ephemeral n8n container
  with `--network none`. Synthetic failures at SMTP, Drive, and NotebookLM each
  produced exactly one `uncertain` result, and none reached a later provider node.
  Provider calls and daemon writes were replaced by local test doubles; no external
  integration was contacted. The receiver tests independently confirm that retry
  requires operator reconciliation.
- Rejection/re-entry was executed through the exported Flux A HTTP Catalog Guard and
  preparation nodes in an ephemeral n8n container on an internal-only Docker network,
  connected to a temporary Daemon and catalog. The rejected document returned zero
  eligible items with reason `ja_existe_no_catalogo`; the workflow completed without
  reaching queue-add (which had an intentionally invalid credential) or invoking Flux
  B. The temporary queue remained empty and production state was untouched (T083/T086).
- Each Flux B failure branch was executed in an ephemeral n8n container against a
  temporary Daemon and an internal synthetic provider returning HTTP 503. Email,
  Drive, and NotebookLM each produced one `uncertain` state; replay and later-stage
  starts were rejected until the operator explicitly reconciled the uncertain step.
  After reconciliation to `not_started`, a controlled retry could start. Exactly one
  synthetic request was made per provider path; no real provider or production data
  was contacted (T087).
- Unauthorized-sender and ambiguous-text decisions are covered by local Hermes helper
  tests only; no such message was sent through active Telegram Long Polling. The
  provider firewall remains inaccessible and unverified.

## 3. Exercise the internal JSON API

Run these calls from a container attached to `rede-automacao`, using the actual daemon
service DNS name. Supply the service credential through a secure shell environment in
the test environment; never commit or print its value.
Use `Authorization: Bearer <role-token>` for add, the Hermes token for
decisions and pending reads, and the Flux B token for approved reads. Never put actual
secret values in the workflow export or command history.

```sh
curl --fail-with-body --silent --show-error \
  --max-time 35 \
  -H "Authorization: Bearer <role-token>" \
  -H "Content-Type: application/json" \
  -d '{"items":[{"title":"Test NFe","date":"2026-10-31","url":"https://example.invalid/nfe/1","file_path":"/configured-pdf-root/test.pdf","document_id":"test-nfe-1"}]}' \
  "http://<daemon-service>:8555/api/queue/add"

curl --fail-with-body --silent --show-error \
  --max-time 35 \
  -H "Authorization: Bearer <role-token>" \
  "http://<daemon-service>:8555/api/queue/list?state=pending"
```

Expected: both success responses have `Content-Type: application/json`; the second
response includes exactly one pending test document. Retry the add request and verify
that it does not create a duplicate.

Approve using the batch ID returned by `queue/list` and the allowed chat:

```sh
curl --fail-with-body --silent --show-error \
  --max-time 35 \
  -H "Authorization: Bearer <role-token>" \
  -H "Content-Type: application/json" \
  -d '{"batch_id":"<batch-id>","action":"approve","chat_id":123456789}' \
  "http://<daemon-service>:8555/api/queue/resolve"

curl --fail-with-body --silent --show-error \
  --max-time 35 \
  -H "Authorization: Bearer <role-token>" \
  "http://<daemon-service>:8555/api/queue/list?state=approved&batch_id=<batch-id>"
```

Expected: the batch no longer appears as pending and remains available to Flux B as
approved, with its PDF paths intact. An approval from any other chat must be rejected.

For rejection, use a separate test batch with `"action":"reject"`. Expected: the
document title appears in the official catalog format and the batch disappears from
pending; retrying rejection must not create another catalog entry.

For the Flow B contract, call `record_step` with `outcome=started` before each external
effect and `outcome=succeeded` only after its success is confirmed. Use `uncertain`
when the provider result is ambiguous; do not retry that effect automatically.
`not_started` and reconciliation of an uncertain successful outcome require the
operator credential. Call `complete` only after email, Drive, and NotebookLM all
succeeded. Terminal batch/action results are retained in the daemon queue file so
retries remain idempotent.

The local implementation smoke test was run against the daemon on
`127.0.0.1:8555`, using temporary catalog, queue, and PDF files. It exercised add/list,
approval/rejection, all delivery steps, completion, idempotent retries, and JSON
content types; it sent no email or external integration requests.

Initial remote validation record (2026-10-31; superseded by the current record above):
the candidate was staged separately on VPS `nx01` and all 24 tests passed there with
Python 3.12. The deployed daemon script was
then backed up to
`/home/ubuntu/scripts/notebooklm_receiver.py.backup-20261031-1456`, replaced with the
validated candidate, and restarted through `notebooklm-sync.service`. The service is
active. At that initial point the queue routes returned structured JSON but failed
closed with `503 AUTH_NOT_CONFIGURED`; the role-scoped tokens were configured later.
The legacy catalog filter returned JSON successfully during a non-mutating smoke
check. No email or provider action was performed. The temporary test copy was removed
after validation.

## 4. Validate the Telegram and n8n journeys

1. Submit one fresh, one older-than-five-days, one catalogued, and one duplicated
   document through ChangeDetection/Flux A.
2. Verify only the fresh, uncatalogued document is downloaded and added to pending.
3. Verify Hermes uses Telegram Long Polling, sends a prompt with the titles, dates, and
   batch ID, and accepts only a reply to that prompt from chat `123456789`.
4. Send invalid text and a response from another chat; confirm the queue remains
   pending and Flux B is not triggered.
5. Approve from the allowed chat; confirm the pending state moves to approved, Hermes
   invokes only the internal Flux B webhook, and Flux B reads that approved batch.
6. With provider integrations stubbed, assert the order email -> Drive -> NotebookLM;
   assert all three completed states before the daemon removes the approved batch and
   records it in the catalog.
7. Interrupt each stage. Confirm completed steps are not repeated. If a stage is left
   with an uncertain external result, confirm it is held for reconciliation rather
   than automatically replayed.
8. For the email node, assert To contains only `operador@exemplo.com.br`, Cc and Bcc are
   empty, and a wrong recipient configuration fails the test.

## 5. Verify timeout and JSON error behavior

For every daemon outbound HTTP client and subprocess, inspect or test that a timeout of
at least 30 seconds is explicitly configured. Trigger representative invalid input,
unauthorized access, missing batch, conflicting transition, and internal persistence
failure. Expected: each response has a structured JSON error body; no API route returns
generic HTML, and failed mutations do not report success.

Latest execution record (2026-11-02):

- `python -m unittest discover -s tests -v` passed all 44 tests; the focused
  `test_hermes_gatekeeper.py` run passed all 14 tests.
- Read-only inspection of VPS `nx01` confirmed the `nfe-gatekeeper-poll` Hermes job
  is enabled at a one-minute interval. The installed helper and notification state
  file are present in the active `hermes-agent` container; the production pending
  queue was empty when checked.
- The polling tests use a synthetic pending batch and confirm the first poll notifies,
  a repeat reports `already_notified`, and only one Telegram send is attempted. No
  production batch was added or prompted during this check.
- The 14 focused unit tests reject ambiguous text, a mismatched reply, and an
  unauthorized chat in the workspace Gatekeeper helper. The deployed helper differs:
  its CLI exposes only `poll-once`, `decide`, and `test-send`; `decide` does not accept
  the actual sender, reply target, or original text and supplies the authorized chat ID
  itself. Therefore these local tests do not validate sender/reply enforcement in the
  active runtime. No ambiguous Telegram reply or unauthorized-sender message was sent;
  reconcile the deployed helper before claiming T089 complete.
- Host UFW reports `inactive`; no provider firewall CLI/configuration is available
  through the VPS session, so provider ingress/egress remains unverified (T090).
- T091 is validated by the active one-minute poll schedule plus the synthetic polling
  and deduplication test. Flux A does not send Telegram directly; Hermes discovers
  pending batches through the Daemon queue.

Reconciliation follow-up:

- The isolated Flux A receiver test exercises the real catalog-filter and queue-add
  endpoints with temporary catalog, queue, and PDF-cache paths. Its matrix includes
  a fresh item twice, an old item, a catalogued item, and a previously rejected item;
  one item enters a single pending batch and no distribution is triggered. This
  validates the Daemon boundary and idempotency, but is not a full execution of the
  exported workflow in an isolated n8n engine; T094 remains open for that runtime
  validation.
- `test_n8n_workflow.py` now asserts that no workflow node or node configuration uses
  SQLite for business/queue state and that queue operations remain delegated to the
  Daemon. The complete local suite passed all 46 tests.
- The active `hermes-agent` bind-mounted helper and skill were backed up under
  `/opt/data/backups/nfe-gatekeeper-pre-reconcile-20261102/` and atomically replaced
  with the workspace versions. The deployed CLI exposes `decide-message`; Python
  syntax and CLI startup passed, and a synthetic unauthorized sender was rejected
  before any Daemon request. The container remained running. No Telegram message,
  queue decision, or Flux B request was sent during deployment validation.
- T092 remains open: no real ambiguous or unauthorized Telegram reply was sent,
  because this requires an explicitly authorized test interaction. T093 also remains
  open because provider firewall settings are not accessible from the VPS session.

Latest follow-up (2026-11-02):

- T098 was executed through the exported Flux A webhook in a disposable n8n 2.45.7
  container on an internal-only Docker network, with a temporary Daemon, catalog,
  queue, PDF cache, and synthetic document/PDF origin. The matrix supplied one
  eligible document twice, one six-day-old document, one catalogued document, and one
  previously rejected document. The actual workflow filter reported two eligible
  instances and rejected the other three; queue-add stored one item and skipped the
  duplicate. The workflow ended at `Confirmar Lote para Hermes`, wrote one synthetic
  PDF, and executed no email, Drive, or NotebookLM node. The catalog contained only
  its two seeded fixtures, and the isolated n8n container had no published ports or
  production network attachment.
- The full-path run exposed a binary-field mapping bug: `Preparar Arquivo da Fila`
  returned the PDF metadata without the `anexo_arquivo` key, so the shared-file node
  could not write it. The workflow now returns `binary:{anexo_arquivo:binary}` and
  `test_n8n_workflow.py` guards this mapping. The corrected exported workflow run
  completed successfully; `python -m unittest discover -s tests -v` passed all 47
  tests.
- The deployed helper was also given synthetic `decide-message` inputs in the active
  Hermes container: an unauthorized chat and ambiguous text were both rejected
  before any Daemon decision. This validates the helper guards, not delivery through
  active Telegram Long Polling. No Telegram message was sent; T097 remains open until
  both cases can be exercised through the active interaction using explicitly
  authorized test accounts. T099 remains open because provider firewall access is
  still unavailable.

Latest T100/T101 follow-up (2026-11-02):

- With explicit authorization, a clearly synthetic batch was presented to the
  allowlisted Telegram chat `123456789` through the active Long Polling connector.
  The helper used a temporary, internal-only fake Daemon; the production pending
  queue remained empty and `NFE_ENABLE_FLUX_B_TRIGGER=false`.
- After the authorized user replied ambiguously (`Talvez`), the active Hermes path
  issued two `/api/queue/resolve` requests with `action=approve`. The fake Daemon
  returned `409` for both, so its synthetic batch remained pending; no production
  state or Flux B action occurred. This initial attempt failed guard validation:
  the active path attempted approval despite ambiguous text. The T102 follow-up
  below records the remediation and successful retest.
- No non-allowlisted account was authorized, so no message was sent from an
  unauthorized sender. That part of T100 remains untested. The active skill was
  restored byte-for-byte, the exact temporary test process was stopped, and its
  named temporary files were removed.
- Provider firewall rules could not be inspected because read-only panel/API access
  was unavailable. Host UFW status does not establish provider ingress/egress;
  T101 remains an external operational limitation.

Latest T102 implementation and validation (2026-11-02):

- Removed the action-only `decide <batch_id> <action>` CLI command. The only
  decision command is now `decide-message`, which receives the actual sender chat,
  reply target, and verbatim text; it accepts only normalized exact `Aprovar` or
  `Rejeitar` before resolving the oldest pending batch. The active Hermes skill
  prohibits direct calls to `/api/queue/resolve` and prohibits inferring or
  rewriting a decision.
- Added regressions proving `Talvez` exits before any Daemon request and the old
  action-only CLI command is rejected. All 48 local tests passed.
- Promoted the helper and skill atomically to the active Hermes data mount after
  creating a protected backup at
  `/home/ubuntu/hermes-data/backups/nfe-gatekeeper-pre-t102-20261102-0707/`.
  The deployed CLI no longer lists `decide`; an isolated invocation with
  `Talvez` returned an explicit validation error before loading secrets.
- Repeated the authorized Long Polling test with a synthetic batch and fake Daemon.
  The authorized user replied `Talvez`; the fake queue remained pending and logged
  zero `/api/queue/resolve` requests. The production pending queue remained empty,
  Flux B remained disabled, and the active Hermes container stayed running.
- T102 is complete for ambiguous-reply handling. An unauthorized Telegram sender
  was not tested because no non-allowlisted test account was authorized; that
  remains an open validation item. Provider firewall access also remains
  unavailable (T101/T103).

External operational limits for T104/T105 (2026-11-02):

- T104 cannot be completed without an explicitly authorized test account whose
  Telegram chat is not allowlisted. No such account or authorization was provided,
  so no unauthorized-sender message was sent. The existing allowlist remains
  restricted to chat `123456789`; local helper rejection tests do not substitute
  for this active Long Polling check.
- T105 cannot be completed without read-only access to the provider firewall
  panel/API or an operator-provided redacted rules export. Neither was available
  in this session. Host UFW being inactive and Docker network observations do not
  prove the provider's ingress/egress rules; no claim that the firewall was
  inspected or compliant is made.
- Keep T104 and T105 open until the required authorization/access is supplied and
  the checks are actually performed. These are external operational prerequisites,
  not implementation failures in the local workspace.

NotebookLM failure report implementation (2026-11-02):

- The local Flux B error branch records the affected document titles, Google Drive
  IDs, and available error text with the approved batch. The report API exposes only
  those report fields to Hermes; it does not expose PDF paths.
- Hermes delivers the report only to chat `123456789` through the existing outbound
  Long Polling connector. The one-minute poller acknowledges a report only after
  Telegram confirms delivery. If delivery or acknowledgement fails, the report stays
  available for a later poll; a crash between send and acknowledgement can cause a
  duplicate message.
- Since Drive completes before NotebookLM starts, the alert says the files remain in
  Drive. The batch stays in `needs_reconciliation`; neither n8n nor Hermes retries
  NotebookLM automatically. The error text is bounded and common credential labels
  are redacted before it is stored for Telegram delivery.
- Deployment update (2026-11-02): `notebooklm-sync.service` was restarted with the
  workspace Daemon, and the active Hermes helper and skill were replaced with their
  workspace versions. SHA-256 checks confirmed all three deployed files match the
  workspace. The service is active, listens on its existing internal bind address, and
  an authenticated Hermes query returned zero pending failure notifications. No test
  poll or Telegram message was sent.
- The original n8n workflow remains unchanged: its published version still has 14
  nodes and its latest draft still has 18. A separate inactive review copy named
  `Monitoramento SEFAZ NFe - Homologacao` (ID
  `237c400d337a4edf`) was imported into the same project with 56 nodes and 45
  connections. It uses the reconciled Flux B failure path, preserves the existing
  SMTP/Google Drive credential references, restricts email to the test recipient, and
  keeps the direct Telegram node disabled. Service URLs use the existing internal
  Daemon address. The copy has not been activated or executed; no production
  distribution or Telegram message occurred. T112 is complete; controlled end-to-end
  failure validation remains open in T113.
- Protected backups of both original workflow versions, the imported review copy, and
  the active Daemon/Hermes files are at
  `/home/ubuntu/scripts/.nfe-deploy-backups/nfe-notebooklm-alert-20261102T154646Z`.
- `python -m unittest discover -s tests -q` passed all 52 tests. The first full run had
  one transient `WinError 10053` in an API test; all 12 API tests passed on isolated
  rerun and the full suite passed on retry. The failure-report Code node also passed a
  JavaScript syntax check, and a synthetic mixed-result check confirmed that a failed
  document is reported without marking the batch successful. No Telegram message or
  production NotebookLM operation was executed.

T113 operational follow-up (2026-11-03):

- The n8n editor was reached through a temporary HTTPS tunnel. A protected backup of
  the remote Compose file was created at
  `/home/ubuntu/scripts/.nfe-deploy-backups/nfe-notebooklm-alert-20261102T154646Z/docker-compose.before-t113-url-fix.yml`.
  The editor URL/proxy settings were corrected for HTTPS while the existing
  `WEBHOOK_URL` was preserved. This configuration still points to a temporary tunnel;
  replace it with a stable HTTPS domain or restore the backup after T113 is finished.
- The homologation copy was published to disable only the Changedetection trigger and
  keep its direct Telegram node disabled. An invalid-token probe then failed at
  `Validar Token Hermes` with `access to env vars denied`; n8n 2.x blocks environment
  variable access in workflow expressions by default. No email, Drive upload,
  NotebookLM sync, or Telegram notification occurred.
- The copy was unpublished after the failed probe. The original remains active; the
  homologation copy is inactive, its Changedetection trigger is disabled, its Flux B
  webhook node is enabled but unpublished, and its Telegram node remains disabled.
  The post-unpublish webhook probe returned 404.
- The pending batch and failure-notification queues are empty, Hermes Flux B triggering
  remains disabled, and no execution is in progress. No synthetic batch or PDF was
  created. T113 remains open until Flux B authentication is moved to n8n-managed
  credentials; do not disable the instance-wide environment-access protection.
- Native credentials applied (2026-11-03): three n8n Header Auth credentials were
  created server-side from the existing container values without printing them
  (`NFE Daemon Flow A`, `NFE Daemon Flow B`, `NFE Hermes Webhook`; database backup in
  `/home/ubuntu/scripts/.nfe-deploy-backups/nfe-native-creds-20261103T181357Z`).
  `alerta_zen_atual.json` no longer uses `$env`: the Flux B webhook uses Header Auth and
  every Daemon HTTP node uses its credential; the redundant `Validar Token Hermes` and
  `Responder Nao Autorizado` nodes were removed. The homologation copy was re-imported
  inactive. The production workflow still uses `$env` and must be replaced after the
  homologation rehearsal passes.
- T113 rehearsal passed (2026-11-03). Webhook rejects missing/invalid tokens (403). A
  first synthetic batch exposed `Access to the file is not allowed` on the PDF read
  node; fixed with `N8N_RESTRICT_FILE_ACCESS_TO=/home/node/.n8n-files;/home/ubuntu/nfe-pdf-cache`
  (Compose backup `docker-compose.before-file-access.yml`). That batch stayed
  approved and was not replayed (`DELIVERY_REQUIRES_RECONCILIATION`). A second batch,
  approved by the user as a Telegram reply, sent the test email to the fixed address,
  uploaded to Drive, failed NotebookLM via a temporary failing stub, held the batch
  without retry, and the user received the Telegram failure alert listing both
  titles. Reverted afterwards: original NotebookLM script restored (hash verified),
  `NFE_ENABLE_FLUX_B_TRIGGER=false`, homologation copy unpublished, synthetic PDFs
  removed. Pending: delete the test email/Drive files; two synthetic batches remain
  held in the Daemon queue. Production still needs the new workflow, the file-access
  setting (already in Compose), a stable HTTPS domain, and rotation of the n8n
  initial password. T104/T105 remain accepted external limits.
- Production cutover (2026-11-03): after the user chose "replace", the two synthetic
  held batches were removed from the Daemon queue (queue and catalog backed up; catalog
  unchanged) and the production workflow `2SvZmrqtG96GMlFS` ("Alerta Zen com Assinatura
  MPL") was replaced with the 54-node native-credentials version and published.
  Previous workflow export and queue/catalog copies:
  `/home/ubuntu/scripts/.nfe-deploy-backups/prod-cutover-20261103T183928Z`.
  `NFE_ENABLE_FLUX_B_TRIGGER` stays `false`. A manual empty-body probe of
  `/webhook/alerta-zen` ran one execution that stopped at `Obter Pagina` with no queue,
  email, Drive or NotebookLM effect. The homologation copy remains unpublished.

## Production end-to-end validation (2026-11-03)

- Real batch of 7 documents published on 30/10/2026 (5 Technical Notes, 2 Technical Reports) was approved via Telegram reply and delivered: e-mail, Google Drive and NotebookLM.
- Defect found and fixed: the secondary guard in `/api/catalog/filter-new` rejected any new version of an already-catalogued norm. It now compares type (NT/IT), number and version. Regression test added in `tests/test_receiver_filter_regression.py`.
- The first NotebookLM attempt failed with `Authentication expired`; the held batch alert worked. Google cookies were refreshed in `~/.notebooklm/profiles/default/storage_state.json` (only google.com domains) and the 7 Drive files were linked manually. The batch was then closed through the Daemon recovery path (`completing` status) and recorded in the catalog.
- Re-running the Changedetection recheck is safe: all 7 documents are now catalogued.
- NotebookLM cookies expire periodically; the failure alert lists the documents so they can be re-imported from Drive.

### E-mail corrigido e reenviado ao time (2026-11-03)

- O e-mail pos-aprovacao voltou ao layout original (lista [NOVO], botao, assinatura MPL), com titulos lidos de 'Preparar Documentos para E-mail' e destinatarios do time (13).
- Os 7 PDFs de 30/10/2026 foram reenviados por um fluxo temporario avulso: primeiro so ao responsavel, depois aos 12 colegas MPL. O fluxo temporario 'TMP reenvio email' (inativo) deve ser arquivado na interface do n8n.
- Importar um fluxo via 'n8n import:workflow' o desativa; republicar no editor.

### Vigia do NotebookLM (2026-11-03)

- ops/nfe_notebooklm_watchdog.py roda a cada 20 min (systemd timer nfe-notebooklm-watchdog.timer, unit em /etc/systemd/system, script em /home/ubuntu/scripts).
- Executa 'notebooklm auth refresh --verify' para manter a sessao Google viva.
- Se a renovacao falhar, envia UM aviso no Telegram (e outro quando voltar).
- Quando a sessao esta valida e existe lote em needs_reconciliation (email e Drive ok, NotebookLM uncertain, com drive_file_id), vincula os PDFs ao caderno (sem duplicar), registra o passo como operator e conclui o lote.
- Segredos: usa /etc/notebooklm-sync.env (QUEUE_FLOW_B_TOKEN e QUEUE_OPERATOR_TOKEN) via EnvironmentFile.
- Logs: journalctl -u nfe-notebooklm-watchdog.service.

### Conclusão e Estado Final de Produção (2026-11-03)

- Fluxo ativo no n8n renomeado para "SEFAZ NF-e – Monitoramento e Aprovação Manual" (`2SvZmrqtG96GMlFS`), ativo e protegido com a nova senha de proprietário configurada pelo usuário.
- Fluxo temporário de reenvio de e-mail arquivado.
- Todos os 13 destinatários da equipe MPL confirmaram o recebimento do lote de 30/10/2026.
- Caso seja necessária renovação manual de emergência, uma cópia do `cookies.txt` pode ser posicionada na pasta local de backup de credenciais do operador.
- O sistema opera em regime permanente e autônomo.

