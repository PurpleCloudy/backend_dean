import io
import json
import logging
import secrets
import threading
import time
import uuid
import zipfile
from queue import Empty, Queue
from xml.etree import ElementTree

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from dean_agent.agent import ChatSessionError, ask_agent
from dean_agent.config import settings
from dean_agent.db import SessionLocal, engine
from dean_agent.models import AuditLog, Regulation, RegulationChunk, SQLChangeProposal
from dean_agent.sql_changes import SQLChangeError, decide_sql_change, propose_sql_change
from dean_agent.vector_search import index_regulation, qdrant_client


app = FastAPI(title="Dean Agent API", version="0.1.0")
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: uuid.UUID | None = None


class SQLProposalRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=4000)
    explanation: str = Field(min_length=15, max_length=2000)
    reason: str = Field(min_length=10, max_length=2000)


class SQLDecisionRequest(BaseModel):
    approve: bool
    reason: str = Field(min_length=10, max_length=2000)


def require_approval_token(token: str) -> None:
    if not settings.deanery_approval_token or not secrets.compare_digest(token, settings.deanery_approval_token):
        raise HTTPException(403, "Неверный ключ подтверждения")


@app.get("/health")
def health() -> dict:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok", "database": "postgresql"}


@app.get("/health/vector")
def vector_health() -> dict:
    try:
        client = qdrant_client()
        try:
            client.get_collections()
        finally:
            client.close()
        with httpx.Client(trust_env=False, timeout=5) as http:
            response = http.get(f"{settings.bge_m3_url.rstrip('/')}/health")
            response.raise_for_status()
            bge = response.json().get("status")
    except Exception as exc:
        raise HTTPException(503, f"Векторный поиск недоступен: {type(exc).__name__}") from exc
    if bge != "ok":
        raise HTTPException(503, "BGE-M3 ещё загружается")
    return {"status": "ok", "qdrant": "local", "bge_m3": "remote via SSH tunnel"}


@app.post("/chat")
def chat(body: ChatRequest, x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    if settings.openai_model == "your-tool-calling-model":
        raise HTTPException(503, "Настройте OPENAI_MODEL в .env")
    try:
        return ask_agent(body.message, x_actor_id, body.session_id)
    except ChatSessionError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        logger.exception("Agent chat failed (%s)", type(exc).__name__)
        # Do not expose provider credentials or database connection details.
        raise HTTPException(502, f"Агент не ответил: {type(exc).__name__}") from exc


@app.post("/chat/stream")
def chat_stream(body: ChatRequest, x_actor_id: str = Header(min_length=1, max_length=128)) -> StreamingResponse:
    """Server-sent events: status, session, tool_call, tool_result, done/error."""
    if settings.openai_model == "your-tool-calling-model":
        raise HTTPException(503, "Настройте OPENAI_MODEL в .env")

    def events():
        messages: Queue[tuple[str, dict]] = Queue()
        started = time.monotonic()

        def emit(event: str, data: dict) -> None:
            messages.put((event, data))

        def run() -> None:
            try:
                result = ask_agent(body.message, x_actor_id, body.session_id, trace_callback=emit)
                emit("done", result)
            except ChatSessionError as exc:
                emit("error", {"type": type(exc).__name__, "message": str(exc)})
            except Exception as exc:
                logger.exception("Agent stream failed (%s)", type(exc).__name__)
                emit("error", {"type": type(exc).__name__, "message": "Агент не ответил"})

        threading.Thread(target=run, daemon=True).start()
        while True:
            try:
                event, data = messages.get(timeout=15)
            except Empty:
                event, data = "status", {"phase": "waiting", "elapsed_s": int(time.monotonic() - started),
                                         "message": "Агент ещё работает"}
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            if event in {"done", "error"}:
                break

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: uuid.UUID, x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    raise HTTPException(410, "Старый маршрут относится к демонстрационной схеме; используйте /sql-proposals")


@app.post("/proposals/{proposal_id}/decision")
def decide(proposal_id: uuid.UUID, x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    raise HTTPException(410, "Старый маршрут относится к демонстрационной схеме; используйте /sql-proposals")


@app.post("/sql-proposals")
def create_sql_proposal(body: SQLProposalRequest,
                        x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    with SessionLocal() as db:
        try:
            proposal = propose_sql_change(db, sql_text=body.sql, explanation=body.explanation,
                                          reason=body.reason, actor_id=x_actor_id,
                                          agent_run_id="manual-sql")
        except SQLChangeError as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(422, f"Проверка SQL не прошла ({type(exc.orig).__name__})") from exc
        return {"id": str(proposal.id), "status": proposal.status,
                "explanation": proposal.explanation, "reason": proposal.reason,
                "preview": proposal.preview}


@app.get("/sql-proposals/{proposal_id}")
def get_sql_proposal(proposal_id: uuid.UUID,
                     x_approval_token: str = Header(min_length=1),
                     x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    require_approval_token(x_approval_token)
    with SessionLocal() as db:
        proposal = db.get(SQLChangeProposal, proposal_id)
        if proposal is None:
            raise HTTPException(404, "Предложение не найдено")
        return {"id": str(proposal.id), "status": proposal.status, "sql": proposal.sql_text,
                "explanation": proposal.explanation, "reason": proposal.reason,
                "preview": proposal.preview, "created_by": proposal.actor_id,
                "decided_by": proposal.decided_by, "decision_reason": proposal.decision_reason}


@app.post("/sql-proposals/{proposal_id}/decision")
def decide_sql_proposal(proposal_id: uuid.UUID, body: SQLDecisionRequest,
                        x_approval_token: str = Header(min_length=1),
                        x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    require_approval_token(x_approval_token)
    with SessionLocal() as db:
        try:
            proposal = decide_sql_change(db, proposal_id=proposal_id, approver=x_actor_id,
                                         decision_reason=body.reason, approve=body.approve)
        except SQLChangeError as exc:
            db.rollback()
            raise HTTPException(409, str(exc)) from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise HTTPException(409, f"Изменение не применено ({type(exc.orig).__name__})") from exc
        return {"id": str(proposal.id), "status": proposal.status,
                "preview": proposal.preview}


def chunks_from_upload(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    elif filename.lower().endswith(".txt"):
        pages = [(None, data.decode("utf-8"))]
    elif filename.lower().endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                with archive.open("word/document.xml") as document:
                    xml = document.read(20_000_001)
            if len(xml) > 20_000_000:
                raise HTTPException(413, "Слишком большой текст внутри DOCX")
            root = ElementTree.fromstring(xml)
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = ["".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
                          for paragraph in root.findall(".//w:p", namespace)]
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise HTTPException(422, "Не удалось прочитать DOCX") from exc
        pages = [(None, "\n".join(paragraphs))]
    else:
        raise HTTPException(415, "Поддерживаются PDF, DOCX и UTF-8 TXT")
    chunks = []
    for page, content in pages:
        content = " ".join(content.split())
        for start in range(0, len(content), 1200):
            part = content[start:start + 1200]
            if part.strip():
                chunks.append((page, part))
    if not chunks:
        raise HTTPException(422, "Текст не найден; для сканов нужен OCR")
    return chunks


@app.post("/regulations")
async def upload_regulation(file: UploadFile = File(...),
                            title: str | None = Form(default=None, max_length=255),
                            source: str | None = Form(default=None, max_length=512),
                            x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    data = await file.read(10_000_001)
    if len(data) > 10_000_000:
        raise HTTPException(413, "Максимальный размер 10 МБ")
    filename = (file.filename or "document").split("/")[-1].split("\\")[-1]
    try:
        chunks = chunks_from_upload(filename, data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(422, "Не удалось прочитать документ") from exc
    with SessionLocal() as db:
        doc = Regulation(title=title or filename, source=source or filename, content="")
        db.add(doc)
        db.flush()
        db.add_all(RegulationChunk(regulation_id=doc.id, page=page, content=content)
                   for page, content in chunks)
        db.add(AuditLog(actor_id=x_actor_id, agent_run_id="manual-upload",
                        action="upload_regulation", entity_type="regulation",
                        entity_id=str(doc.id), old_value=None,
                        new_value={"title": doc.title, "source": doc.source, "chunks": len(chunks)}))
        db.commit()
        doc_id = doc.id
        doc_title = doc.title
    try:
        index_regulation(doc_id)
    except Exception as exc:
        logger.exception("Failed to index regulation %s", doc_id)
        with SessionLocal.begin() as db:
            db.get(Regulation, doc_id).index_status = "failed"
        return JSONResponse(status_code=202, content={
            "id": doc_id, "title": doc_title, "chunks": len(chunks),
            "index_status": "failed", "error_type": type(exc).__name__,
        })
    return {"id": doc_id, "title": doc_title, "chunks": len(chunks), "index_status": "ready"}


@app.get("/regulations/{regulation_id}")
def get_regulation(regulation_id: int, x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    with SessionLocal() as db:
        doc = db.get(Regulation, regulation_id)
        if doc is None:
            raise HTTPException(404, "Документ не найден")
        return {"id": doc.id, "title": doc.title, "source": doc.source,
                "index_status": doc.index_status}


@app.post("/regulations/{regulation_id}/reindex")
def reindex_regulation(regulation_id: int,
                       x_actor_id: str = Header(min_length=1, max_length=128)) -> dict:
    with SessionLocal() as db:
        if db.get(Regulation, regulation_id) is None:
            raise HTTPException(404, "Документ не найден")
    try:
        count = index_regulation(regulation_id)
    except Exception as exc:
        logger.exception("Failed to reindex regulation %s", regulation_id)
        with SessionLocal.begin() as db:
            db.get(Regulation, regulation_id).index_status = "failed"
        raise HTTPException(503, f"Индексация не удалась: {type(exc).__name__}") from exc
    return {"id": regulation_id, "index_status": "ready", "chunks": count}
