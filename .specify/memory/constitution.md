<!--
Sync Impact Report
Version change: 0.0.0 (uninitialized scaffold) -> 1.0.0
Modified principles: placeholder principles 1-5 -> Security-First and Human Approval;
  Exclusive Gatekeeper and Role Separation; Internal Network and State Isolation;
  Secure, ARM64-Compatible Infrastructure; Bounded Execution and Structured APIs
Added sections: Architecture and Infrastructure Constraints; Development and Compliance Workflow
Removed sections: None
Deferred items: TODO(RATIFICATION_DATE): original adoption date is unknown
-->

# Automação NFe (nx01) Constitution

## Core Principles

### I. Security-First and Human Approval
Security is a non-negotiable design requirement. Every corporate notification and email
MUST receive prior human approval; no workflow may send or notify before that approval.
Approval status and the authorization decision MUST be enforced by Hermes, not inferred
from workflow progress or user-provided message content.

### II. Exclusive Gatekeeper and Role Separation
Hermes is the exclusive Gatekeeper for approval and authorization decisions. n8n MUST act
strictly as a collector and distributor: it may collect inputs and route approved work, but
MUST NOT approve, authorize, or independently initiate corporate notifications or email.
This separation prevents orchestration logic from bypassing the human-in-the-loop control.

### III. Internal Network and State Isolation
Container-to-container communication MUST use only the internal Docker network
`rede-automacao`. The n8n internal SQLite database MUST NOT store application state,
queues, or business rules. Intermediate state MUST be stored in isolated files managed
through the Python microservice (Daemon Python), keeping operational state outside n8n's
internal database.

### IV. Secure, ARM64-Compatible Infrastructure
Deployments MUST NOT expose any new external firewall port. Telegram integrations MUST
use outbound Long Polling exclusively and MUST NOT use public webhooks. Every Docker image
used by the project MUST natively support ARM64 (Ampere A1), so deployments remain
compatible with the target VPS architecture.

### V. Bounded Execution and Structured APIs
Every script and daemon MUST configure explicit timeouts for its external and potentially
blocking operations. API responses MUST use structured JSON payloads; generic HTML error
pages are not valid API responses. Failures MUST be represented as explicit, structured
errors rather than success-shaped responses.

## Architecture and Infrastructure Constraints

- Services MUST preserve the role boundary: n8n collects and distributes; Hermes alone
  gates approval and authorization.
- Internal service communication MUST remain on `rede-automacao`. Public exposure of
  services or creation of new inbound firewall openings is prohibited.
- Persistent n8n SQLite storage MUST NOT be repurposed for business state, queues, or
  rules. Intermediate state files MUST be isolated and managed by Daemon Python.
- Telegram connectivity MUST be initiated through outbound Long Polling. Public Telegram
  webhooks are prohibited.
- Docker images MUST provide native ARM64 support for Ampere A1; emulation alone does
  not satisfy this requirement.

## Development and Compliance Workflow

- Changes to workflows, services, scripts, or infrastructure MUST preserve the human
  approval gate and the exclusive Hermes Gatekeeper role.
- Validation MUST cover applicable network boundaries, state-storage boundaries,
  configured timeouts, JSON response contracts, and ARM64 image compatibility.
- Reviews and deployment checks MUST verify that no new external firewall port or public
  webhook was introduced and that no application state, queue, or business rule was
  written to n8n's internal SQLite database.
- Any exception to a non-negotiable principle requires a constitution amendment before
  implementation; implementation details or operational convenience do not constitute an
  exception.

## Governance

This constitution is the governing standard for project architecture and delivery.
Amendments MUST document their rationale and impact, be reviewed against existing
principles and constraints, and receive project-owner approval before adoption. Each
change MUST update the version and amendment date. Compliance MUST be checked during
design, review, and deployment; non-compliant changes MUST be corrected before release.

Versioning follows semantic versioning: MAJOR for incompatible changes to or removal of
governance requirements, MINOR for new principles or materially expanded requirements,
and PATCH for clarifications that do not change obligations.

**Version**: 1.0.0 | **Ratified**: TODO(RATIFICATION_DATE): original adoption date unknown | **Last Amended**: 2026-10-31
