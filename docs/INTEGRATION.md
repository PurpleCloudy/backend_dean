# React integration

API prefix: `/api/v1`. Interactive contract: `/docs`; machine-readable schema: `/openapi.json`; current permitted resources: `/api/v1/schema`.

## Login and session lifecycle

Send `POST /auth/login` with JSON `{ "login": "admin", "password": "…" }`, an allowed `Origin`, and `credentials: "include"`. The response contains `access_token`, `token_type`, `expires_in`, `csrf_token`. Keep the bearer access token and CSRF token in memory. The refresh token is an HttpOnly SameSite=Strict cookie restricted to `/api/v1/auth`; production mode also requires Secure.

Authenticated calls use `Authorization: Bearer …`. Refresh uses `POST /auth/refresh`, `credentials: "include"`, the same allowed Origin, and `X-CSRF-Token`. Replace the access token with the returned value. Serialize refresh calls across your React client: reusing the previous refresh cookie revokes the whole session family. Logout is `POST /auth/logout`; all sessions can be revoked with `/auth/logout-all`.

The backend reconstructs roles and relationships from the database for every request. Actor, role, institute, student and session ownership supplied in arbitrary client headers confer no authority.

## Resources and workflows

`GET /resources/{name}?limit=50&offset=0` returns `{items,limit,offset,has_more}`. Equality filters use documented projected fields. Limits are1–500, offsets0–100000. Mutation keys, including every composite-key component, are query parameters. JSON decimal values are strings; dates use ISO8601. Omitted PATCH fields preserve values; explicit null is accepted only where the contract permits it.

Every canonical table has an explicit purpose. Protected movement, grades, order relationships, accounts and audit are not interchangeable generic CRUD operations. Use the documented workflow endpoints and `Idempotency-Key` for repeatable commands. A conflicting reuse returns409. Current OpenAPI and `/schema` must be consulted for exact permitted DTOs and roles; the independent gate checks their agreement with behavior.

Future student orders return `status: "pending"`, `event_id`, `job_id` and `effective_date`. Future enrollment creates the student's record immediately in status `Зачислен`; its effective transition occurs on the order date. Other future movements preserve current status until their effective date. Read current state through `/workflows/scheduled/{event_id}` and `/jobs/{job_id}`. Pending movements can use `cancel_scheduled`; the source policy rejects cancellation of a pending enrollment. Completed orders remain auditable.

A closed grade is corrected through `/workflows/request_grade_correction` and `/workflows/decide_grade_correction`. The requester cannot approve their own request. An actual institute director or deputy decides it; the application administrator role alone does not grant this position. Stale grade snapshots and superseded attempts are rejected. The request and decision preserve the original grade history and audit.

Errors have `error.code`, a safe `error.message`, optional details and `request_id`; preserve the request ID for diagnosis. Do not display raw upstream SQL or internal trace data.

## Files and jobs

`POST /files` is multipart (`file`, `title`, optional `source`, `purpose`, `association`, `institute_id`) and requires `Idempotency-Key`. The response202 contains the file/version and processing job. Poll `GET /jobs/{id}`. Quarantined/scanning/parsing/indexing files cannot be downloaded or searched. A successful job is required before downloading through `GET /files/{file}/versions/{version}/download`.

New original versions use `POST /files/{file}/versions`. Original bytes remain immutable. PDF/DOCX/TXT parsing does not provide OCR; extraction flags report incomplete or unverified text. File associations and institute access are checked again against current permissions. Report exports are durable jobs created with `POST /exports`; a completed artifact still requires current report access.

## Original agent JSON and streaming

`POST /agent/chat` and `POST /agent/chat/stream` accept `{message, session_id?, attachments?}`. Without attachments, JSON preserves `{run_id,session_id,answer,proposals,tools_used}`. Streaming is POST SSE, so use `fetch` with a reader rather than native EventSource. Events include `status`, `session`, `tool_call`, `tool_result`, `done`, `error`; parse complete blank-line-delimited events across arbitrary network chunks. Save the returned session UUID only within the authenticated user's context.

AbortController stops the client connection. It does not promise that the original synchronous upstream thread stops immediately. Do not automatically retry a chat POST after timeout. Proposal preview does not apply data; view and decide through `/agent/proposals/{id}` and `/decision` with a reason and idempotency key. Approval rechecks scope and the current target snapshot. A proposal involving a closed grade creates a correction request; it does not bypass the independent decision workflow.

The adapter and service credentials are internal. Browser clients must never call upstream `/chat` directly or possess delegation, storage, database or provider secrets.

## Chat attachments

Read authenticated `GET /agent/capabilities` before enabling attachment submission. Its `attachment_context` reports protocol `v1`, `backend_supported`, `agent_supported`, `agent_status`, and limits: five versions, 20,000,000 combined bytes and at most50 text chunks per internal retrieval. The installed original agent currently reports `agent_supported: false`. The backend preserves and serves attachment context for a compatible future agent; the original agent does **not** yet understand attachments. Its requests with attachments return412 `agent_attachments_unsupported` before a run or model call is admitted. If upstream capability discovery is unavailable or malformed, the attached chat POST returns503; public capability discovery still returns200 with `agent_status: "unavailable"`. Text-only chat remains available with an otherwise healthy original agent.

Upload and wait for processing first, then send exact immutable references:

```json
{
  "message": "Объясни этот документ",
  "attachments": [
    {"file_id": "<file UUID>", "version_id": "<ready version UUID>"}
  ]
}
```

`attachments` defaults to an empty list. Version IDs must be unique; each version must belong to its file, be ready, and remain accessible to the current user. An older ready version is valid even when a newer version is active. Invalid mixed lists are rejected as one request. File references are never inferred from a filename, a URL or the last message in the conversation.

For an admitted attachment request, JSON responses and SSE `session`/`done` events add `attachment_context` containing `backend_run_id`, `canonical_request_id`, `session_id` and ordered attachment metadata. The existing top-level `run_id` identifies the upstream agent run; it is a different identifier. Use `GET /agent/runs/{backend_run_id}/attachments` to retrieve the backend context. This is owner-only and rechecks current file permissions and readiness. A post-admission error provides the backend `run_id` and `session_id` in error details or the SSE error event so the client can recover this context without resubmitting the chat.

Metadata contains only `file_id`, `version_id`, `ordinal`, `title`, `source`, `filename`, `mime`, `byte_size`, `sha256`, `quality` and `text_available`. The canonical request links the attachment to the exact admitted user request. Original bytes, file identity and this binding are immutable; storage paths and credentials are not returned.

A compatible server-side agent receives the references in its signed request. After the normal claim, it signs a fresh token for each read, preserving the trusted `user`, `session`, `run` and `auth_session` claims and using audience `deanery-attachments`. The endpoints are `/api/v1/internal/agent/attachments`, `/{version_id}/text?offset=0&limit=20` and `/{version_id}/download` under that prefix. Text offset is0–10000, limit1–50; responses contain `version_id`, `chunks` (`ordinal`, `page`, `text`), `next_offset`, `quality` and `text_available`. Every request rechecks the live login session, principal, exact run membership and file access. CSV exports have no extracted chunks: `text_available: false`, `chunks: []`, `next_offset: null`; their original bytes remain downloadable. PDF/DOCX/TXT extraction quality is reported without claiming OCR or complete understanding.

The upstream capability handshake is a signed `GET /health/scoped` with audience `deanery-health`. A compatible implementation returns `{"status":"ready","adapter":"scoped-v1","capabilities":{"attachments_context_v1":true}}`. Set this flag only after the implementation actually consumes the supplied context; changing the flag alone does not implement attachment understanding. The legacy response without `capabilities` means unsupported. A malformed capability value, including explicit null, makes capability discovery unavailable. The installed adapter returns the same envelope with `attachments_context_v1: false` and independently rejects attached chat requests before forwarding them to the original service.
