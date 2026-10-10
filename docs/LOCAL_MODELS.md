# Local BGE-M3 and LM Studio

This setup is for the Windows development machine, with BGE on CPU and Qwen on
the GPU. The vendored `references/dean-agent` runtime is retained byte-for-byte
from its pinned upstream revision; deployment controls live in the adapter.

## BGE-M3

The local service is `integrations/bge_service.py`. It exposes:

- `GET http://127.0.0.1:8231/health`: healthy only after loading the model.
- `POST http://127.0.0.1:8231/predict`: the existing backend/agent contract,
  `{"text":["..."],"return_dense":true,"return_sparse":true,"return_colbert":false}`.
  The response contains `vector` (1024-dimensional dense rows) and `sparse`
  (token ID to weight dictionaries).

The service uses one inference at a time, four CPU threads and an internal batch
size of one. An overlapping request gets 503 with `Retry-After: 1`; health checks
remain available. Input is limited to eight texts per request, 32,768 characters
and 8,192 tokens per text. Oversized texts are rejected, not silently truncated.
ColBERT output is not used by this project. The service has no authentication
and binds only to loopback; it is a local development service.

Installed weights: [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3), revision
`5617a9f61b028005a4858fdac845db406aefb181`.
The 2,271,145,830-byte `pytorch_model.bin` has verified SHA-256
`b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`.
Weights, the environment, logs and local settings are ignored under `.local/`.

Reproduce installation from the project root in PowerShell:

```powershell
py -3.12 -m venv .local/bge-venv
./.local/bge-venv/Scripts/python.exe -m pip install -r integrations/bge-requirements.txt
./.local/bge-venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-m3', revision='5617a9f61b028005a4858fdac845db406aefb181', local_dir='.local/bge/model', allow_patterns=['*.json','*.pt','*.bin','*.model'], ignore_patterns=['onnx/*'], max_workers=2)"
```

Run in a terminal, or use the same command in a hidden background process:

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
./.local/bge-venv/Scripts/python.exe -m integrations.bge_service
```

Only one instance should run. On the configured machine it was started in the
background; logs are `.local/bge/stdout.log` and `.local/bge/stderr.log`.
It is not registered for automatic startup after reboot.

## LM Studio and the QA stack

The installed model is `lmstudio-community/Qwen2.5-7B-Instruct-GGUF/`
`Qwen2.5-7B-Instruct-Q4_K_M.gguf`. It was loaded with GPU offload and a
16,384-token context. Its API identifier is `qwen2.5-7b-instruct`.

```powershell
lms load lmstudio-community/Qwen2.5-7B-Instruct-GGUF/Qwen2.5-7B-Instruct-Q4_K_M.gguf --exact --context-length 16384 --gpu max --identifier qwen2.5-7b-instruct --yes
lms server start --port 1234
```

The existing `deanery-frontend-qa` stack now uses a local Compose override at
`.local/bge/compose.real.yaml`:

- API and worker `BGE_URL=http://host.docker.internal:8231`.
- Agent `BGE_M3_URL=http://host.docker.internal:8231`.
- Agent `OPENAI_BASE_URL=http://host.docker.internal:1234/v1` and
  `OPENAI_MODEL=qwen2.5-7b-instruct`.
- A separate `deanery_documents_bge_m3` Qdrant collection holds real embeddings.
  The previous deterministic test collection is preserved. All three existing
  ready regulations were reindexed through the API; a fourth explicitly
  fictional control regulation was uploaded through the normal processing queue.

Apply this configuration without exposing the private environment contents:

```powershell
docker compose --env-file .local/frontend_verification/runtime.env -f .local/frontend_verification/compose.yaml -f .local/bge/compose.real.yaml up -d --no-deps api worker agent
```

This changes only the QA stack on port 18000, used by React on port 5173.
Other Compose stacks are not switched. Recreating QA containers without the
override restores the provider settings from its original environment file.

## Verification on 2026-10-10

Local evidence is in `.local/bge/`:

| Check | Result | Evidence |
|---|---|---|
| Download integrity and installed dependency consistency | PASS | `model-revision.txt`, `installed-requirements.txt`; `pip check` |
| Real CPU inference, finite 1024-dimensional vectors and nonempty sparse weights | PASS | `real-bge-check.json` |
| Russian semantic comparison | Related cosine 0.737, unrelated 0.327; three texts in about 2.1 seconds including validation requests | `real-bge-check.json` |
| Five invalid HTTP requests | All rejected with 422 | `real-bge-check.json` |
| Qwen tool call and use of its returned value | PASS | `lmstudio-preflight.json` |
| Docker access to both model services | HTTP 200 | Live container requests |
| Reindex three regulations and scan/extract/embed/index new regulation | PASS | `index-check.json` |
| React chat clicks: search the control regulation | Found 17 days and МАЯК-7429, cited its source | `browser-rag.png`, `browser-rag.txt`, `browser-chat-results.json` |
| Reload React and reopen chat | Real conversation restored | `browser-history-reload.json` |
| Natural-language count of study groups | FAILED: Qwen invented `groups`, omitted LIMIT initially, then stopped after the table was rejected | `sql-diagnostic.sse` |
| React chat with an explicitly supplied valid SQL count | PASS: 33 groups, independently matched against PostgreSQL | `browser-sql-guided.png`, `browser-sql-guided.txt` |
| Automated backend regression, including new service contract/error tests | 70 passed, 32 opt-in integration tests skipped | `backend-regression.log` |

Successful installation and document retrieval do not prove reliable SQL planning
by this local model. The failed count query remains visible in the evidence.
The backend correctly rejected it. No access controls or SQL restrictions were
relaxed to make the model pass.

This run does not repeat the entire frontend matrix, prove multiuser throughput,
or verify maximum-length inference. The machine has 16 GB RAM and little free
memory with Docker and both models running. At that earlier checkpoint chat
attachments were unsupported; document retrieval used the regulations index.

## Attachment profile update

The integration now vendors upstream `d92bf5fbfc5b37c3236a6454aeffa5374fe88b11`
and consumes immutable attachments through signed, ACL-checked reads. See
[INTEGRATION.md](INTEGRATION.md#chat-attachments) for current limits and the
distinction between file storage, text reading and visual-model support.
Qwen2.5-7B-Instruct is text-only: the default profile rejects photos and scan-only
PDF chat before admission. Do not set AGENT_VISION_MODEL for this model.

The installed Gemma3-12B-Instruct-QAT Q4_0 is listed by LM Studio as vision-capable
but not trained for tool use. It is only a candidate for a separate bounded
visual check; its presence does not establish compatibility with the full agent
tool loop. Any temporary test unloads Qwen first and must restore Qwen afterwards.
No new model download or second simultaneous model is required by this update.
Actual results and limitations are recorded separately in the attachment
verification report; no success is implied by this configuration description.

The first real five-long-TXT test used the loaded16k Qwen context and the actual
scanner, worker, BGE and file store. All five header codes were answered correctly
without a context overflow. The model did not call the available continuation
tool and gave the wrong code for a fact beyond the initial excerpt. Therefore
full-document reading and natural pagination did not pass this test. The failure
is retained in `.local/attachment_integration/real-five-long-texts.json`; no
successful retake replaces it. Native user history contained only the human
question and canonical history retained all five exact file versions.

An actual browser stream subsequently exercised the continuation tool and caught
a separate integration defect: the old database CHECK rejected its tool name.
Revision0006 adds that provenance value while retaining the old names and size
bound. Apply the current migration head before this agent update. PostgreSQL
callback/SSE/history regression after the fix passed19 checks (one separate
deterministic-provider proposal test was skipped); this does not certify the
model's answers. Existing reports for schema0005 describe earlier checkpoints.

The installed Gemma trial was **not run**: after unloading Qwen, free physical
RAM was1.61GiB, below the agreed5GiB safety threshold (GPU memory6.76GiB was
sufficient). Gemma was never loaded, and no image/scan inference occurred.
Qwen was restored with its original identifier,16384 context and GPU offload;
readiness returned200/schema0006 and visual capability remained false. No other
applications or BGE services were stopped to free memory. Evidence:
`.local/attachment_integration/vision-trial.json`. This is a hardware-limit result,
not a measured failure of Gemma's visual or tool protocol. Visual understanding
therefore remains unverified on this prototype.
