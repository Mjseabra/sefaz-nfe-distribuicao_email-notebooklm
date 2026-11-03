# Research: Aprovação Manual de Notificações SEFAZ NFe

**Date**: 2026-10-31

## Scope and repository findings

The workspace has no application source, deployment configuration, tests, receiver,
catalog, or workflow exports. The user-designated `notebooklm_receiver.py` and
`notebooklm_catalog.json` must be located in the deployed project before implementation.
No framework, runtime minor version, catalog shape, PDF staging path, or test runner can
be verified from this checkout.

## Decisions

### 1. Keep the queue outside n8n SQLite and make each file update atomic

- **Decision**: The Python daemon is the only owner of queue/catalog JSON. Serialize
  the entire read/validate/transition/write transaction using an exclusive lock on a
  separate stable lock file. Write a securely created temporary file in the same
  directory, flush and `fsync` it, replace the canonical JSON with `os.replace`, and
  `fsync` the containing directory. Keep invalid files for operator recovery; return an
  explicit JSON error and never replace unreadable state with an empty queue.
- **Rationale**: Concurrent n8n/Hermes requests must not lose updates or leave truncated
  JSON. Same-directory replacement is atomic on the same filesystem. Locking a separate
  stable file continues to coordinate writers even when replacing the data file.
- **Alternatives considered**: Writing directly to the live JSON file is simpler but can
  corrupt all queued work on interruption; SQLite would be transactional but violates
  the project constitution and explicit design.
- **Implementation gate**: Confirm the daemon's existing process model and file-system
  volume. Linux `flock` is advisory and only works when every writer follows the same
  lock protocol; verify semantics if the volume is on a network filesystem.
  References: [Python `fcntl.flock`](https://docs.python.org/3/library/fcntl.html#fcntl.flock),
  [Python `os.replace`](https://docs.python.org/3/library/os.html#os.replace),
  [Python `tempfile`](https://docs.python.org/3/library/tempfile.html),
  [Linux `fsync(2)`](https://man7.org/linux/man-pages/man2/fsync.2.html),
  [Linux `rename(2)`](https://man7.org/linux/man-pages/man2/rename.2.html).

### 2. Distinguish pending from approved state within the managed queue

- **Decision**: Keep `pending` and `approved` batches in the daemon-managed
  `fila_pendencias.json`. Approval atomically removes a batch from pending and moves it
  to approved; Flux B reads only approved batches. Rejection records document titles in
  the catalog before removing the pending batch. Successful completion records the
  titles as processed before removing the approved batch.
- **Rationale**: Literally deleting approved documents before Flux B reads them would
  lose their file paths and make distribution impossible. A state transition empties
  the pending inbox while retaining a durable distribution work item. The same file
  allows a single atomic state replacement for the approval transition.
- **Alternatives considered**: Clearing the queue on approval and passing all document
  paths in the Hermes webhook payload conflicts with the instruction that Flux B reads
  paths from the queue. A second approved-work file introduces a non-atomic two-file
  move and requires recovery for split state.
- **Compatibility note**: This preserves the existing feature spec's requirement that
  approved work remains recoverable until distribution succeeds. `resolve` therefore
  has `approve`, `reject`, distribution-stage, and `complete` actions; "approve" means
  remove from pending, not destroy the approved work item.

### 3. Make rejection and completion retry-safe

- **Decision**: Use stable batch and document identifiers. Write catalog changes
  idempotently before removing the corresponding queue state. Persist an intermediate
  `rejecting` or `completing` status in the queue before updating the catalog; on
  restart or retry, resume the catalog write and final queue removal. Retried requests
  for an already-applied action return the current result without duplicating catalog
  entries. Return success only after durable commit.
- **Rationale**: A crash between catalog and queue writes may leave a document in both
  places. Staging the operation makes the work invisible as pending/approved and
  recoverable; idempotent catalog updates make replay safe.
- **Alternatives considered**: Remove from the queue first risks permanently losing the
  document if catalog persistence fails. Two different JSON files cannot be updated in
  one atomic filesystem operation; a durable journal or staged intent is required.
- **Implementation gate**: Preserve the deployed catalog format and confirm its
  identity/deduplication rules; only the Spec Kit workspace was available for inspection.
  References: [RFC 9110 idempotent methods](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2),
  [Python `json`](https://docs.python.org/3/library/json.html).

### 4. Bind Telegram decisions to the correct batch and authorized chat

- **Decision**: Hermes accepts commands only from chat `123456789`, only for the oldest
  pending batch, and only as a reply to a prompt containing that batch ID. It polls
  Telegram using outbound Long Polling and polls the daemon over the private Docker
  network. A restarted Hermes can re-present the same oldest pending batch; duplicate
  prompts do not create a second batch or change state.
- **Rationale**: Restricting the chat and binding the reply to a stable batch prevents
  unauthorized or out-of-order decisions. Queue polling does not require an inbound
  webhook or a new listener port.
- **Alternatives considered**: Public Telegram webhooks violate the constitution;
  allowing a free-standing "Aprovar" without a batch context can resolve the wrong
  request when several batches are waiting.
- **Implementation gate**: Confirm that the deployed Hermes/Telegram integration can
  inspect chat IDs, message replies, and Long Polling updates before implementation.

### 5. Treat workflow side effects as ordered, persisted steps

- **Decision**: Flux B executes email, Drive upload, and NotebookLM sync in that order.
  It records `started` before and `succeeded` after each operation through the daemon.
  Completed stages are skipped on retry. An operation left `started` after a crash is
  uncertain and requires reconciliation before it can be repeated.
- **Rationale**: Persisted progress avoids automatically repeating known-completed
  uploads or synchronization. Email delivery cannot be guaranteed exactly once across
  a process crash without a provider idempotency contract.
- **Alternatives considered**: Retrying every failed workflow from the first step risks
  duplicate mail and uploads; removing the queue before all steps risks data loss.
- **Test guard**: In all implementation and test configurations, the resolved email
  recipient is exactly `operador@exemplo.com.br`; no To, Cc, or Bcc value can add anyone.

### 6. Keep service traffic private while permitting required outbound integrations

- **Decision**: All service-to-service API calls use the user-defined Docker bridge
  `rede-automacao`; do not publish new host ports. Keep integration traffic outbound
  only and restrict egress to required destinations where deployment policy supports
  it. Prefer one shared private bridge over a second shared network. If the deployed
  bridge is marked `internal: true`, use an existing controlled egress gateway rather
  than attaching peer service containers to a second shared network.
- **Rationale**: Outbound Long Polling and cloud distribution need egress, while no
  public Telegram webhook or new inbound firewall port is needed. Docker user-defined
  bridge networks support outbound connectivity by default; `internal: true` instead
  isolates the network externally and prevents direct provider access. Telegram's
  `getUpdates` uses bot-initiated Long Polling and cannot be used while an outgoing
  webhook is configured.
- **Alternatives considered**: A public webhook requires inbound reachability; placing
  every integration on an internet-disconnected internal network prevents required
  integrations. A second shared egress network can permit unintended peer-to-peer paths.
- **Implementation gate**: Inspect the deployed Docker network and firewall rules.
  Preserve the intended internal-only container-to-container path and verify outbound
  egress without opening an inbound port. References:
  [Docker bridge networking](https://docs.docker.com/engine/network/drivers/bridge/),
  [Compose networks](https://docs.docker.com/reference/compose-file/networks/),
  [Telegram getUpdates](https://core.telegram.org/bots/api#getupdates),
  [Telegram getting updates FAQ](https://core.telegram.org/bots/faq#how-do-i-get-updates).

### 7. Use explicit timeouts and JSON for every daemon API outcome

- **Decision**: Set HTTP and subprocess timeouts to at least 30 seconds; allow a longer
  HTTP client timeout than the Telegram Long Polling wait. Return structured JSON for
  success, validation, authorization, conflict, and server errors.
- **Rationale**: Bounded operations prevent hung daemons, while a consistent JSON
  contract lets Hermes and n8n distinguish failures without interpreting HTML.
- **Alternatives considered**: Framework-default timeouts and HTML error pages are
  unpredictable and violate project requirements.

## Remaining implementation gates

- Locate the actual receiver, catalog, n8n exports, Hermes configuration, and Docker
  deployment before implementation.
- Verify runtime/framework versions, file locking requirements, path and file
  permissions, catalog schema, current network egress, and Telegram reply capabilities.
- Keep secrets outside source control and use the deployment's existing secret mechanism.
- There is no specified throughput or latency target; do not add a queue database or
  external broker to optimize an unmeasured workload.
