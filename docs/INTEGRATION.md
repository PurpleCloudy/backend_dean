# React integration

API prefix: `/api/v1`. Interactive contract: `/docs`; machine-readable schema: `/openapi.json`; current permitted resources: `/api/v1/schema`.

Current deployment migration head is `0006`; readiness requires it. The conversation endpoints were introduced in `0004`. Revision0006 adds `read_attachment_text` to the bounded durable tool-provenance allowlist, preserving the previous three names and300-entry limit. Upgrade before enabling the updated attachment agent; without it, a reader completion cannot be saved. No historical answers, file bindings or access policies are rewritten.

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

`v_employees` has exactly one row per visible `employee_id`. Its `unit` field is a stable semicolon-separated list of all current organizational affiliations: teaching department, dean's office, then every institute directed by that employee. An employee with no such affiliation has null `unit`. The same institute can appear with distinct dean's-office and director roles; neither role is silently discarded. Migration `0005` fixes row multiplication when a director heads multiple institutes without changing the view's columns, types or invoker permissions.

## Files and jobs

`POST /files` is multipart (`file`, `title`, optional `source`, `purpose`, `association`, `institute_id`) and requires `Idempotency-Key`. The response202 contains the file/version and processing job. Poll `GET /jobs/{id}`. Quarantined/scanning/parsing/indexing files cannot be downloaded or searched. A successful job is required before downloading through `GET /files/{file}/versions/{version}/download`.

New original versions use `POST /files/{file}/versions`. Original bytes remain immutable. PDF/DOCX/TXT parsing does not provide OCR; extraction flags report incomplete or unverified text. File associations and institute access are checked again against current permissions. Report exports are durable jobs created with `POST /exports`; a completed artifact still requires current report access.

## Original agent JSON and streaming

`POST /agent/chat` and `POST /agent/chat/stream` accept `{message, session_id?, attachments?}`. Without attachments, JSON preserves `{run_id,session_id,answer,proposals,tools_used}`. Streaming is POST SSE, so use `fetch` with a reader rather than native EventSource. Events include `status`, `session`, `tool_call`, `tool_result`, `done`, `error`; parse complete blank-line-delimited events across arbitrary network chunks. Save the returned session UUID only within the authenticated user's context.

AbortController stops the client connection. It does not promise that the original synchronous upstream thread stops immediately. Do not automatically retry a chat POST after timeout. Proposal preview does not apply data; view and decide through `/agent/proposals/{id}` and `/decision` with a reason and idempotency key. Approval rechecks scope and the current target snapshot. A proposal involving a closed grade creates a correction request; it does not bypass the independent decision workflow.

The adapter and service credentials are internal. Browser clients must never call upstream `/chat` directly or possess delegation, storage, database or provider secrets.

## Durable conversations

Migration `0004` exposes the existing server conversation data. The browser must use these authenticated endpoints instead of treating localStorage as the chat database:

| Endpoint | Result |
| --- | --- |
| `GET /agent/sessions?limit=50&offset=0` | `{items, next_offset}`; most recently active sessions first, UUID as a stable tie-breaker. Limit1–100. |
| `GET /agent/sessions/{id}?limit=100&offset=0` | Session summary plus `{messages, next_offset}`; messages in chronological turn order. Limit1–200. Follow `next_offset` until null to load the whole history. |
| `PATCH /agent/sessions/{id}` with `{title}` | Updated summary. Trimmed title must contain1–160 characters. |
| `DELETE /agent/sessions/{id}` |204 after hiding the conversation. A running or ambiguous turn returns409 `session_busy`. |

Summary fields are `id`, `title`, `created_at`, `updated_at`, `preview`, `busy`. `busy` includes both `running` and `ambiguous` runs. It is not an indication that the browser still has an SSE connection. The normal run recovery procedure remains necessary if the upstream process was terminated without a final callback. No separate creation request is required: the first admitted chat POST creates the session, and its returned UUID is the conversation ID. A fresh unsent draft remains a browser draft.

Message fields are `id` (opaque stable string), `role` (`user`, `assistant`, `error`), `content`, `created_at`, `run_id`, `status`, `request_id`, `attachments`, `unavailable_attachment_count`, `proposals`, `unavailable_proposal_count`, `tools_used`. `run_id` and `request_id` here identify the **backend** run; legacy messages have null run/status/request IDs. An admitted prompt appears immediately with `running` status, and failed/ambiguous prompts remain visible after reload. Completion updates the durable outcome even when the browser disconnects. Do not blindly repeat a timed-out message. An admitted upstream error includes the session/backend run IDs in error details or the SSE error event; validation failures before admission have no stored turn.

Attachments use the same `AttachmentMetadata` shape as chat responses (`byte_size`, not `size`). Proposals carry the current `ProposalResponse`, including the current decision status. Both metadata types recheck current permissions; inaccessible entries are omitted and reported by their unavailable counts. The already saved free-text conversation remains the owner's historical conversation; it is not recalculated when underlying domain data changes. Old runs did not persist tool names, so they expose an empty `tools_used` list rather than invented trace data.

Successful managed history comes from existing `backend.agent_runs` and its canonical `deanery.agent_request`, while the original agent continues to save and read `public.chat_messages` for model context. This avoids duplicate inserts. For compatibility, raw legacy messages **before the first canonical managed run in that session** are also returned; a session with no canonical runs returns all its legacy user/assistant messages. Direct unsigned upstream writes after a session became managed are unsupported. Their raw rows are not deleted or rewritten by migration. For text-only turns the adapter uses the most recent20 successfully saved raw messages for model context; attachment turns additionally keep only whole recent pairs within the byte bound described below. API history pagination itself has no silent message truncation.

History is strictly owned: another user receives404 even when that user is an application administrator. Removal is a tombstone, not erasure of retained audit/request/proposal/file records; removed sessions cannot be reopened or reused by chat POST. Existing browser-only drafts, custom local titles, and local traces are not imported or deleted automatically. Database rows, completed answers and already stored legacy messages survive the additive migration. Deploy API and adapter together after migrating to the current head; readiness checks that revision. Prefer a forward application fix while retaining the additive schema and metadata. An older application image requires a compatibility patch for its exact-revision readiness check and tombstone filtering; simply rolling back the image is not supported. Downgrading chat metadata is deliberately refused because dropping tombstones would resurrect removed conversations.

## Local model services

See [LOCAL_MODELS.md](LOCAL_MODELS.md) for the installed CPU BGE-M3 service,
LM Studio configuration, reproducible dependencies and real-model verification
results, including the unresolved natural-language SQL planning limitation.

## Chat attachments

Read authenticated `GET /agent/capabilities` before enabling attachment submission. The updated pinned agent consumes signed current-run context. Its `attachment_context` retains protocol `v1`, `backend_supported`, `agent_supported`, `agent_status`, five versions, 20,000,000 combined bytes and `max_text_chunks: 50` for the internal retrieval API. Additional fields distinguish deployment capabilities: `vision_supported`, `model`, `accepted_mime_types` (chat with this model), `upload_supported_mime_types` (storage), `max_file_bytes: 10000000`, `max_message_chars: 2000` for attached messages only, `max_initial_text_chunks_per_file: 1`, `max_tool_text_chunks: 2`, and `max_visual_pages: 10`. Ordinary messages retain their8000-character limit. Excess attached prompt length returns422 `attachment_message_too_long` before admission.

The default local Qwen profile is text-only: chat accepts TXT/CSV/DOCX/PDF with available text. Images and scan-only PDFs return412 `agent_vision_unsupported` before a run is admitted. Text PDFs with unextracted graphics remain readable with an explicit quality warning; DOCX embedded images are not consumed. Upload/storage also support PNG/JPEG/WEBP independently of the current model. A ready zero-chunk image/scan reports `quality.text_available: false`, `ocr_performed: false` and incomplete extraction. Existing version quality may omit text_available/null; per-run metadata always rechecks actual chunks. Zero chunks do not produce embeddings. Original bytes remain immutable/private.

An older agent advertising no attachment support still causes412 `agent_attachments_unsupported`. Malformed/unavailable capability discovery causes503 on attached POST; public discovery returns200 with `agent_status: "unavailable"` and no accepted chat formats. Text-only chat remains compatible.

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

The upstream handshake is signed `GET /health/scoped` with audience `deanery-health`. Its envelope includes `capabilities: {"attachments_context_v1": true, "attachments_vision_v1": false, "model": "qwen2.5-7b-instruct"}`. Legacy capability envelopes remain valid and default to no visual support. A malformed value, including explicit null/string booleans or visual support without a model identity, is unavailable. The adapter's `AGENT_VISION_MODEL` must equal its exact configured model identifier to enable a separately verified visual profile; absent/mismatched values stay false. This is an operator deployment assertion, not automatic proof that an arbitrary provider supports vision/tools. The local default leaves it unset.

Initial context contains one complete1200-character chunk per file, at most6000 source characters for five files, plus metadata and honest next_offset. `read_attachment_text` reads1–2 chunks per call, at most5 additional chunks per run; `context_budget_exhausted` explicitly means unread content remains, not end-of-file. Export CSVs have no index: the adapter verifies/downloads originals on each read and applies the same bounded paging. Uploaded CSVs use the ordinary parser. Each later tool read still checks current login/run/file permissions.

Only attachment runs trim model-facing old history to newest whole user/assistant pairs within6000 UTF-8 bytes. Current user blocks, system prompt and tools are preserved; stored chat history is untouched. These are conservative character/byte bounds, not a tokenizer guarantee for arbitrary long conversations. Prepared file excerpts/metadata/images are not persisted in native user history: it contains exactly the original user's message. Previously delivered assistant responses remain normal history and may quote facts already shown. A fresh file read on a later turn requires reattachment and current ACL. No history reset or retroactive erasure is performed.

Images and scanned PDF pages are decoded/rendered only in a short-lived Linux subprocess with768MB address-space,30CPU-second,35wall-second and12MB output bounds. Input files remain limited to10MB; images to25MP, one frame and2048px output; PDFs to500 source pages and the first10 visual pages, with explicit visual_truncated. The signed adapter disables all direct multipart/upload routes of the upstream service. Arbitrary URLs and prepared browser image blocks are never accepted.

The vendored runtime exactly matches upstream commit `d92bf5fbfc5b37c3236a6454aeffa5374fe88b11`; only the external integration adapter applies these controls. Pillow12.3.0 declares MIT-CMU; PyMuPDF1.28.2 declares AGPL-3.0 or Artifex commercial licensing. The upstream MIT notice does not relicense those dependencies; commercial distribution requires a separate review of the applicable dependency terms.
