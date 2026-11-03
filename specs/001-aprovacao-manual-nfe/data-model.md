# Data Model: Aprovação Manual de Notificações SEFAZ NFe

## Storage ownership

The Python receiver exclusively owns mutable business state. It stores queue state in
`fila_pendencias.json` and catalog entries in the existing `notebooklm_catalog.json`,
on an isolated persistent volume managed by the daemon. n8n SQLite is not a source of
business state, queue state, or approval decisions. File paths below are logical names;
the deployed volume location must be discovered before implementation.

The daemon validates each update, serializes the whole read/transition/write operation
under a stable lock, and atomically replaces JSON files using a same-directory temporary
file. If state cannot be parsed or safely persisted, it returns an error and does not
report a successful transition or treat unreadable state as an empty queue.

## Entities

### Document

| Field | Type | Required | Validation / meaning |
|---|---|---:|---|
| `document_id` | string | Yes | Stable identity; derive from the deployed catalog's existing identity rules. |
| `title` | string | Yes | Non-empty display title; also recorded in the catalog on rejection/completion. |
| `date` | string | Yes | Valid source document date in an unambiguous ISO-8601 representation. |
| `url` | string | Yes | Source URL retained for reference and existing collection rules. |
| `file_path` | string | Yes | Existing PDF under the configured staging directory; reject traversal/out-of-root paths. |
| `delivery` | object | Yes for approved batches | Per-step state for email, Drive, and NotebookLM. |

Documents older than five days, already cataloged, or already present in pending or
approved work are not added. The exact identity comparison must preserve the deployed
catalog's existing format; title-only matching is a risk if titles are not unique.

### Batch

| Field | Type | Required | Validation / meaning |
|---|---|---:|---|
| `batch_id` | string | Yes | Stable, unique ID created once for the collected group. |
| `created_at` | string | Yes | ISO-8601 timestamp used to order oldest pending batch first. |
| `status` | enum | Yes | `pending`, `rejecting`, `approved`, or `completing`; membership and status must agree. |
| `items` | array of Document | Yes | Non-empty, unique document list; immutable after Hermes presents it. |
| `decision_at` | string | Approved only | ISO-8601 timestamp of valid human approval. |
| `decision_chat_id` | integer | Approved only | Must equal `123456789` for a valid Telegram decision. |
| `delivery_status` | enum | Approved only | `ready`, `in_progress`, `needs_reconciliation`, or `complete`. |

A batch without a decision remains pending indefinitely. Only the oldest `pending`
batch is offered to Hermes at a time; later batches remain queued so a generic command
cannot resolve multiple unseen batches. `rejecting` and `completing` are durable
intermediate states; they are excluded from Hermes and Flow B reads and resumed after a
restart.

### Delivery step

Each approved document tracks these ordered stages:

1. `email`
2. `google_drive`
3. `notebooklm`

Each stage is `not_started`, `started`, or `succeeded`. The daemon permits a stage to
start only after the previous stage succeeds. A stage left `started` after an
interruption is uncertain; it must not be replayed automatically. Mark it successful
after operator/provider reconciliation, or reset it only when the operation is
confirmed not to have taken effect.

Email recipients are not mutable queue data. During implementation and tests, Flux B's
email node is statically constrained to the single address `operador@exemplo.com.br`.

### Catalog entry

| Field | Type | Required | Validation / meaning |
|---|---|---:|---|
| `title` | string | Yes | Rejected/completed title required by the user design. |
| `document_id` | string | Recommended | Stable identity for safe deduplication where supported by existing schema. |
| `url` | string | Recommended | Source reference when supported by existing schema. |
| `status` | enum | Recommended | `rejected` or `processed`, if the deployed schema supports status values. |
| `recorded_at` | string | Recommended | ISO-8601 time of catalog update, if supported. |

Before writing, implementation must inspect and preserve the existing catalog schema.
For rejection, catalog persistence precedes queue removal. Catalog writes are
idempotent; if queue removal fails after catalog update, the batch remains recoverable
and can be safely resolved again.

## Queue file shape

The logical `fila_pendencias.json` document contains separate arrays for `pending` and
`approved` batches plus a schema version. Approval atomically moves one batch between
arrays. This empties the pending inbox while retaining the approved file paths for Flux
B; completion removes the approved batch only after all three distribution stages and
catalog recording succeed.

```json
{
  "schema_version": 1,
  "pending": [],
  "approved": [],
  "resolved": {}
}
```

This logical shape is not an instruction to rewrite an existing deployed file without
migration. Unknown fields and current schemas must be inspected first.

## State transitions

```text
collected -> pending -> approved -> delivery in progress -> cataloged -> removed
                 |                           |
                 +-> rejected -> cataloged -> removed
                                             |
                              uncertain external outcome -> needs_reconciliation
```

- `pending -> approved`: requires an explicit valid Telegram decision; Hermes calls
  `resolve(approve)`, and the daemon atomically moves the batch.
- `pending -> rejecting -> removed`: stage rejection before writing the catalog;
  recovery repeats the idempotent catalog update and then removes the batch.
- `approved -> delivery in progress`: Flux B may start only if the daemon reports the
  batch as approved.
- `delivery in progress -> completing -> removed`: allowed only after email, Drive, and
  NotebookLM succeed; stage completion before recording processed documents in the
  catalog, then remove the batch.
- Any unresolved/ambiguous side effect is retained for reconciliation; never infer
  success or retry an uncertain email automatically.
