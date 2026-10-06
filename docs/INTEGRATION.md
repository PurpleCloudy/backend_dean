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

`POST /agent/chat` and `POST /agent/chat/stream` accept `{message, session_id?}`. JSON preserves `{run_id,session_id,answer,proposals,tools_used}`. Streaming is POST SSE, so use `fetch` with a reader rather than native EventSource. Events include `status`, `session`, `tool_call`, `tool_result`, `done`, `error`; parse complete blank-line-delimited events across arbitrary network chunks. Save the returned session UUID only within the authenticated user's context.

AbortController stops the client connection. It does not promise that the original synchronous upstream thread stops immediately. Do not automatically retry a chat POST after timeout. Proposal preview does not apply data; view and decide through `/agent/proposals/{id}` and `/decision` with a reason and idempotency key. Approval rechecks scope and the current target snapshot. A proposal involving a closed grade creates a correction request; it does not bypass the independent decision workflow.

The adapter and service credentials are internal. Browser clients must never call upstream `/chat` directly or possess delegation, storage, database or provider secrets.
