# Internal Distribution Trigger Contract

## Purpose

Hermes invokes the existing n8n Flux B webhook only after the daemon has durably
transitioned a batch from `pending` to `approved`. The deployed webhook URL/path is not
in this workspace and must be discovered from the existing n8n workflow export; do not
create a public webhook or publish a new port.

## Request

- **Method**: `POST`
- **Address**: `POST ${NFE_N8N_INTERNAL_BASE_URL}/webhook/nfe-aprovado-interno`
- **Addressing**: `NFE_N8N_INTERNAL_BASE_URL` must resolve to the existing n8n
  service only from `rede-automacao`; do not use a public URL or add a published port.
- **Authentication**: Hermes-specific service credential, supplied through deployment
  secrets
- **Content-Type**: `application/json`
- **Timeout**: At least 30 seconds

```json
{
  "batch_id": "stable-approved-batch-id"
}
```

The batch ID is the only document-related input Hermes sends to Flux B. Flux B MUST
retrieve titles, metadata, and PDF paths from the daemon's approved queue and verify
that the batch is still approved. It MUST NOT trust file paths or approval state supplied
in the webhook body.

## Response

The workflow returns a structured JSON acknowledgement or a structured JSON error for
authorization, malformed requests, and unapproved batches. The exact deployed response
shape must be preserved if compatible; generic HTML responses are prohibited.

```json
{
  "ok": true,
  "batch_id": "stable-approved-batch-id",
  "status": "accepted"
}
```

Hermes does not retry blindly after an ambiguous response. Flux B MUST treat duplicate
triggers for the same batch idempotently by checking the daemon's persisted state.
Network failure or non-JSON responses are errors and MUST NOT result in approval or
queue deletion.

## Security and delivery rules

- Calls are service-to-service traffic on `rede-automacao`; no host port is published.
- Only the Hermes service credential may invoke the approval-trigger webhook.
- Flux B distributes only daemon-confirmed approved work.
- During implementation and tests, its email recipient is fixed to
  `operador@exemplo.com.br`, with no Cc or Bcc recipients.
