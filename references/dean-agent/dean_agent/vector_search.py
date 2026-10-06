"""BGE-M3 dense+sparse embeddings and local Qdrant hybrid retrieval."""

from dataclasses import dataclass
import math

import httpx
from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from dean_agent.config import settings
from dean_agent.db import SessionLocal
from dean_agent.models import Regulation, RegulationChunk


DENSE_SIZE = 1024
BATCH_SIZE = 8


@dataclass
class Embedding:
    dense: list[float]
    sparse: models.SparseVector


def embed_texts(texts: list[str]) -> list[Embedding]:
    if not texts:
        return []
    with httpx.Client(trust_env=False, timeout=120) as http:
        response = http.post(f"{settings.bge_m3_url.rstrip('/')}/predict", json={
            "text": texts, "return_dense": True, "return_sparse": True,
            "return_colbert": False,
        })
        response.raise_for_status()
        data = response.json()
    dense_rows = data.get("vector")
    sparse_rows = data.get("sparse")
    if not isinstance(dense_rows, list) or not isinstance(sparse_rows, list):
        raise ValueError("BGE-M3 не вернул dense и sparse векторы")
    if len(dense_rows) != len(texts) or len(sparse_rows) != len(texts):
        raise ValueError("BGE-M3 вернул неверное число векторов")
    output = []
    for dense, sparse in zip(dense_rows, sparse_rows):
        if len(dense) != DENSE_SIZE or not all(math.isfinite(float(v)) for v in dense):
            raise ValueError("Неверная размерность или значение dense-вектора")
        if not isinstance(sparse, dict):
            raise ValueError("Неверный sparse-вектор")
        pairs = sorted((int(index), float(value)) for index, value in sparse.items())
        if any(index < 0 or not math.isfinite(value) for index, value in pairs):
            raise ValueError("Неверное значение sparse-вектора")
        output.append(Embedding(
            dense=[float(v) for v in dense],
            sparse=models.SparseVector(indices=[i for i, _ in pairs],
                                       values=[v for _, v in pairs]),
        ))
    return output


def qdrant_client() -> QdrantClient:
    # The local endpoint must bypass macOS/system HTTP proxy settings.
    return QdrantClient(url=settings.qdrant_url, timeout=30, trust_env=False,
                        check_compatibility=False)


def ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(settings.qdrant_collection):
        info = client.get_collection(settings.qdrant_collection)
        dense_config = info.config.params.vectors
        if not isinstance(dense_config, dict) or dense_config["dense"].size != DENSE_SIZE:
            raise ValueError("Коллекция Qdrant имеет несовместимую конфигурацию dense-вектора")
        if "sparse" not in (info.config.params.sparse_vectors or {}):
            raise ValueError("Коллекция Qdrant не содержит sparse-вектор")
        return
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config={"dense": models.VectorParams(size=DENSE_SIZE, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )


def index_regulation(regulation_id: int) -> int:
    with SessionLocal() as db:
        regulation = db.get(Regulation, regulation_id)
        if regulation is None:
            raise ValueError("Документ не найден")
        chunks = db.scalars(select(RegulationChunk).where(
            RegulationChunk.regulation_id == regulation_id).order_by(RegulationChunk.id)).all()
        if not chunks:
            raise ValueError("Документ не содержит текста")
        regulation.index_status = "pending"
        db.commit()

    client = qdrant_client()
    try:
        ensure_collection(client)
        for start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[start:start + BATCH_SIZE]
            vectors = embed_texts([chunk.content for chunk in batch])
            points = [models.PointStruct(
                id=chunk.id,
                vector={"dense": vector.dense, "sparse": vector.sparse},
                payload={"regulation_id": regulation_id, "chunk_id": chunk.id},
            ) for chunk, vector in zip(batch, vectors)]
            client.upsert(collection_name=settings.qdrant_collection, points=points, wait=True)
    finally:
        client.close()
    with SessionLocal.begin() as db:
        db.get(Regulation, regulation_id).index_status = "ready"
    return len(chunks)


def search_regulations_hybrid(query: str, db: Session, limit: int = 5) -> list[dict]:
    vector = embed_texts([query])[0]
    client = qdrant_client()
    try:
        if not client.collection_exists(settings.qdrant_collection):
            return []
        prefetch = [models.Prefetch(query=vector.dense, using="dense", limit=20)]
        if vector.sparse.indices:
            prefetch.append(models.Prefetch(query=vector.sparse, using="sparse", limit=20))
        if len(prefetch) == 2:
            response = client.query_points(
                collection_name=settings.qdrant_collection,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit * 3,
            )
        else:
            response = client.query_points(
                collection_name=settings.qdrant_collection,
                query=vector.dense, using="dense", limit=limit * 3,
            )
    finally:
        client.close()
    results = []
    for point in response.points:
        chunk = db.get(RegulationChunk, int(point.id))
        if chunk is None or chunk.regulation.index_status != "ready":
            continue
        results.append({
            "title": chunk.regulation.title,
            "source": chunk.regulation.source,
            "page": chunk.page,
            "excerpt": chunk.content[:1800],
            "score": round(point.score, 5),
            "retrieval": "bge-m3 dense+sparse RRF" if len(prefetch) == 2 else "bge-m3 dense",
        })
        if len(results) >= limit:
            break
    return results
