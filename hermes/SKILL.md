---
name: nfe-gatekeeper
description: Review SEFAZ NFe pending batches, obtain an explicit approval or rejection from authorized Telegram operator chat, and trigger internal distribution only after approval.
---

# NFe approval Gatekeeper

Use this skill only for the SEFAZ NFe approval queue. Hermes is the sole human
Gatekeeper; do not evaluate fiscal rules or decide for the user.

- Use the already configured Telegram connector in outbound Long Polling mode.
  Never register or use a Telegram webhook.
- Accept a decision only from the authenticated operator chat (configured via environment), and only when
  the message is an explicit reply to the currently presented batch. Pass the
  actual sender chat ID, replied-to batch ID, and verbatim original reply text to
  the `decide-message` command; never infer or rewrite a decision. It rejects
  unauthorized senders, unrelated replies, and ambiguous text before contacting
  the Daemon. Ignore those messages and do not call any decision path for them.
- Read the oldest pending batch through the installed
  `/opt/data/skills/nfe-gatekeeper/nfe_gatekeeper.py poll-once` command. Present
  its batch ID and every document's full title and date; do not omit documents.
- The same polling command also sends pending NotebookLM failure reports to the
  authorized chat. Preserve the reported file titles and error details, explain
  that the Drive step already succeeded and the batch is held for reconciliation,
  and acknowledge a report only after Telegram confirms delivery. A failure report
  is informational; never treat it as an approval or trigger a retry.
- For an explicit decision, run
  `/opt/data/skills/nfe-gatekeeper/nfe_gatekeeper.py decide-message <batch_id> --chat-id <sender_chat_id> --reply-to-batch-id <replied_batch_id> --text <original_reply_text>`.
  This is the only supported decision command. Do not call `/api/queue/resolve`
  directly or use an action-only `decide` command. The helper validates the
  sender, exact reply target, decision text, and oldest pending batch before it
  authenticates to the Daemon. Flux B is triggered only after the Daemon confirms
  approval. Keep
  `NFE_ENABLE_FLUX_B_TRIGGER=false` during stubbed integration testing; in that
  mode approvals are recorded but Flux B is not called.
- Never call Flux B after rejection. Never make a distribution request directly
  from Hermes.
- Use only the secrets loaded by the helper from `/opt/data/.env`. Never print,
  disclose, or place tokens in chat or logs. Require structured JSON responses
  and surface failures; never claim success after an ambiguous response.
- Never send corporate email, upload to Drive, or synchronize NotebookLM from
  Hermes.
